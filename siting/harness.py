"""The harness: hooks, gates, checkpoints and state.

Following NORA (Zhou, Huang, Ning, Wu, Li and Zhang, 2026, arXiv:2605.02092):
skills encode intent, the harness encodes guarantees. Everything in this module
exists so that a guarantee holds regardless of what the agent decides to do, and
so that a run interrupted at any point resumes from exactly where it stopped
rather than starting over or, worse, silently skipping a stage.

The five mechanisms NORA formalises, as implemented here:

    lifecycle hooks          `Hooks`, called at stage boundaries
    safety gates             `Gate`, refuses or escalates before an action
    generator-evaluator      `siting.review`, a separate scoring context
    state persistence        `Handoff`, a resumable record of stage position
    human-in-the-loop        `Checkpoint`, nine named places a person may stop it
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts_from_run_id(run_id: str) -> str:
    """The instant encoded in a run id, as an ISO timestamp.

    Used to recover a generation time from a handoff written before that field
    existed, so an older run directory still resumes without inventing one.
    """
    stamp = run_id.rsplit("-", 1)[-1]
    try:
        return dt.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return _now()


# --------------------------------------------------------------------------- #
# stages
# --------------------------------------------------------------------------- #

class Stage(str, Enum):
    """The pipeline in order. A run resumes at the first stage not completed."""
    RETRIEVE = "retrieve"
    COMPILE = "compile"
    SOLVE = "solve"
    REVIEW = "review"
    EVALUATE = "evaluate"
    SCORE = "score"
    RENDER = "render"
    ISSUE = "issue"

    @classmethod
    def order(cls) -> list["Stage"]:
        return [cls.RETRIEVE, cls.COMPILE, cls.SOLVE, cls.REVIEW,
                cls.EVALUATE, cls.SCORE, cls.RENDER, cls.ISSUE]

    def index(self) -> int:
        return Stage.order().index(self)

    @property
    def rebuilds_state(self) -> bool:
        """Whether resuming has to re-run this stage to restore in-memory state.

        A resume must be honest about what it actually skips. The first five
        stages hold their results in memory rather than on disk, so a resumed run
        re-executes them: retrieval is served from the local cache, so no request
        is re-issued and the provenance record is unchanged, and the remaining
        four are deterministic recomputation over that cached data. The last
        three read from `results.json` and are genuinely skipped.
        """
        return self in (Stage.RETRIEVE, Stage.COMPILE, Stage.SOLVE,
                        Stage.REVIEW, Stage.EVALUATE)


# --------------------------------------------------------------------------- #
# human checkpoints
# --------------------------------------------------------------------------- #

class Checkpoint(str, Enum):
    """Named places where a person may be required before the run continues.

    NORA pauses at nine checkpoints in a research pipeline. These are the nine
    that matter in a decision pipeline: the difference is that most of them do
    not merely pause, they refuse to proceed without a recorded reason, and the
    cost of the resulting decision is carried into the output.
    """
    SCOPE = "scope intake"                          # 1
    SOURCE_AUTHORISATION = "source authorisation"    # 2  key-gated or paid source
    LARGE_DOWNLOAD = "large download"                # 3  above a size threshold
    SYNTHETIC_FALLBACK = "synthetic data fallback"   # 4  never without approval
    OVERRIDE_REVIEW = "override review"              # 5  the four verbs
    GUARDRAIL_BREACH = "guardrail breach"            # 6  coverage cost over tolerance
    EVALUATION_REJECT = "evaluation rejection"       # 7  a check rejected the plan
    LOW_SCORE = "low review score"                   # 8  reviewer below the floor
    ISSUE = "issue confirmation"                     # 9  before anything is written


@dataclass
class Decision:
    """A human answer at a checkpoint. Recorded whether it was yes or no."""
    checkpoint: str
    allowed: bool
    reason: str
    actor: str = "operator"
    ts: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Gate:
    """Safety gates. Refuse, escalate, or allow, and record which.

    A gate never silently permits. Either it allows and says why it was safe, or
    it escalates to a checkpoint and stops until a person answers.
    """

    def __init__(self, non_interactive: bool = True, approvals: dict[str, str] | None = None) -> None:
        self.non_interactive = non_interactive
        self.approvals = approvals or {}
        self.decisions: list[Decision] = []
        self.refusals: list[str] = []

    def _record(self, cp: Checkpoint, allowed: bool, reason: str, actor: str = "harness") -> Decision:
        d = Decision(checkpoint=cp.value, allowed=allowed, reason=reason, actor=actor)
        self.decisions.append(d)
        if not allowed:
            self.refusals.append(f"{cp.value}: {reason}")
        return d

    # --- source access ----------------------------------------------------- #

    def source_access(self, source: str, needs_key: bool, env_var: str | None = None) -> bool:
        """A key-gated source may not be reached without the key actually present.

        The failure mode this closes is a source silently dropping out of the
        analysis. If the key is absent the domain is skipped and the output says
        so, rather than the reader being left to infer it from a missing figure.
        """
        if not needs_key:
            self._record(Checkpoint.SOURCE_AUTHORISATION, True,
                         f"{source} is a public endpoint; no authorisation required")
            return True
        present = bool(env_var and os.environ.get(env_var))
        if present:
            self._record(Checkpoint.SOURCE_AUTHORISATION, True,
                         f"{source} credential found in {env_var}")
            return True
        self._record(Checkpoint.SOURCE_AUTHORISATION, False,
                     f"{source} requires a credential and {env_var} is not set; "
                     f"the source is excluded and its absence is reported")
        return False

    def large_download(self, source: str, bytes_: int, threshold_mb: int = 250) -> bool:
        mb = bytes_ / 1_048_576
        if mb <= threshold_mb:
            self._record(Checkpoint.LARGE_DOWNLOAD, True,
                         f"{source} transfer is {mb:.0f} MB, within the {threshold_mb} MB threshold")
            return True
        key = f"{Checkpoint.LARGE_DOWNLOAD.value}:{source}"
        if key in self.approvals:
            self._record(Checkpoint.LARGE_DOWNLOAD, True, self.approvals[key], actor="operator")
            return True
        self._record(Checkpoint.LARGE_DOWNLOAD, False,
                     f"{source} transfer is {mb:.0f} MB, above the {threshold_mb} MB threshold, "
                     f"and no approval was supplied")
        return False

    def synthetic_fallback(self, what: str) -> bool:
        """Never, without an explicit written approval. A siting recommendation
        built on invented data is worse than no recommendation."""
        key = f"{Checkpoint.SYNTHETIC_FALLBACK.value}:{what}"
        if key in self.approvals:
            self._record(Checkpoint.SYNTHETIC_FALLBACK, True, self.approvals[key], actor="operator")
            return True
        self._record(Checkpoint.SYNTHETIC_FALLBACK, False,
                     f"refused to substitute synthetic data for {what}; a recommendation "
                     f"built on invented data would be worse than no recommendation")
        return False

    def write_path(self, path: Path, run_dir: Path) -> bool:
        """Nothing is written outside the run directory or the local cache.

        The pre-tool-use equivalent: rather than trusting that no stage writes
        somewhere destructive, the path is checked before the write happens.
        """
        p = Path(path).resolve()
        allowed_roots = [run_dir.resolve(), Path("cache").resolve(), Path("runs").resolve()]
        if any(str(p).startswith(str(r)) for r in allowed_roots):
            return True
        self._record(Checkpoint.ISSUE, False,
                     f"refused to write outside the run directory: {p}")
        return False

    def guardrail(self, breached: bool, verdict: str) -> bool:
        """A breach escalates but does not block: the planner has the final word.

        This is the one gate that is deliberately not a veto. Its job is to make
        sure the cost is recorded, not to overrule the person who accepted it.
        """
        if not breached:
            self._record(Checkpoint.GUARDRAIL_BREACH, True, verdict)
            return True
        self._record(Checkpoint.GUARDRAIL_BREACH, True,
                     verdict + " Escalated and recorded; the run was not blocked, "
                               "because the reviewing officer holds the decision.")
        return True

    def to_list(self) -> list[dict[str, Any]]:
        return [d.to_dict() for d in self.decisions]


# --------------------------------------------------------------------------- #
# state persistence
# --------------------------------------------------------------------------- #

@dataclass
class Handoff:
    """Resumable state. Written after every stage, read on restart.

    NORA's handoff.json defeats context anxiety in a long research session. The
    problem here is different in kind but the same in shape: a run touches live
    APIs, and re-running a stage means re-retrieving, which changes the
    provenance record and therefore the hash. Resuming from state keeps a run
    reproducible across an interruption.
    """
    run_id: str
    scope: dict[str, Any]
    stage: str = Stage.RETRIEVE.value
    completed: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    # When this run produced its result. Fixed at creation and reused across a
    # resume: a resumed run is the same run, and a bundle whose cover claims it
    # was generated at the resume time while its run id carries the original
    # timestamp contradicts itself on its own first page.
    generated_at: str = ""
    # What the documents on disk were compiled from: a hash of the results
    # content and the set of formats requested. The render stage skips only when
    # both still match, because it is the files that go stale, not the stage.
    rendered: dict[str, Any] = field(default_factory=dict)
    review_score: dict[str, Any] | None = None
    decisions: list[dict[str, Any]] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    token_notes: dict[str, Any] = field(default_factory=dict)
    recovery: dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=_now)

    PATH = "handoff.json"

    # --- lifecycle --------------------------------------------------------- #

    def done(self, stage: Stage, **artifacts: str) -> None:
        if stage.value not in self.completed:
            self.completed.append(stage.value)
        self.artifacts.update(artifacts)
        nxt = Stage.order()[min(stage.index() + 1, len(Stage.order()) - 1)]
        self.stage = nxt.value
        self.updated_at = _now()
        self.recovery = {
            "resume_at": self.stage,
            "read_first": [v for v in self.artifacts.values()][-3:],
            "note": f"stages {', '.join(self.completed)} are complete and must not be re-run",
        }

    def should_run(self, stage: Stage) -> bool:
        return stage.value not in self.completed

    def write(self, run_dir: Path) -> Path:
        out = run_dir / self.PATH
        out.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        return out

    @classmethod
    def load(cls, run_dir: Path) -> "Handoff | None":
        p = run_dir / cls.PATH
        if not p.exists():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        h = cls(**d)
        if not h.generated_at:
            # A handoff written before this field existed. The run id carries the
            # instant the run began, which is the honest stand-in for it.
            h.generated_at = ts_from_run_id(h.run_id)
        return h


# --------------------------------------------------------------------------- #
# lifecycle hooks
# --------------------------------------------------------------------------- #

class Hooks:
    """Called at stage boundaries. Keeps state on disk and the log honest.

    The pre-stage hook validates that the stage is allowed to run at all; the
    post-stage hook persists what it produced. A run killed between the two
    resumes at the stage that did not complete, never at the one that did.
    """

    def __init__(self, run_dir: Path, handoff: Handoff, gate: Gate,
                 announce: Callable[[str], None] = print) -> None:
        self.run_dir = run_dir
        self.handoff = handoff
        self.gate = gate
        self.announce = announce
        self.log: list[dict[str, Any]] = []

    def pre(self, stage: Stage, note: str = "", force: bool = False) -> bool:
        """Returns whether the caller should do the work.

        False means genuinely skip. A stage that rebuilds in-memory state returns
        True even when already complete, and says so, because claiming to skip
        work that is in fact re-executed would make the log untrue.

        `force` re-runs a completed stage whose output has been superseded. The
        render stage uses it: a document compiled from a results file that has
        since changed has to be rebuilt, however complete the stage is marked.
        """
        if force and not self.handoff.should_run(stage):
            self.announce(f"[{stage.value}] complete in a previous run, but its "
                          f"output is out of date; rebuilding"
                          + (f" ({note})" if note else ""))
            self.log.append({"ts": _now(), "stage": stage.value,
                             "event": "rebuild-forced", "note": note})
            return True
        if self.handoff.should_run(stage):
            self.log.append({"ts": _now(), "stage": stage.value,
                             "event": "start", "note": note})
            return True
        if stage.rebuilds_state:
            self.announce(f"[{stage.value}] complete in a previous run; "
                          f"recomputing from cache to restore state")
            self.log.append({"ts": _now(), "stage": stage.value, "event": "rebuild"})
            return True
        self.announce(f"[{stage.value}] complete in a previous run; skipped")
        self.log.append({"ts": _now(), "stage": stage.value, "event": "skipped"})
        return False

    def post(self, stage: Stage, **artifacts: str) -> None:
        self.handoff.done(stage, **artifacts)
        self.handoff.decisions = self.gate.to_list()
        self.handoff.refusals = list(self.gate.refusals)
        self.handoff.write(self.run_dir)
        self.log.append({"ts": _now(), "stage": stage.value, "event": "done",
                         "artifacts": list(artifacts)})

    def stop(self, memory_path: Path | None = None) -> None:
        """Session termination. Writes the audit log and a human-readable memory
        note, so an interrupted run leaves an account of itself behind."""
        (self.run_dir / "hook_log.json").write_text(
            json.dumps(self.log, indent=2), encoding="utf-8")
        if memory_path is None:
            memory_path = self.run_dir / "MEMORY.md"
        h = self.handoff
        lines = [
            f"# Run state: {h.run_id}",
            "",
            f"- Stage on exit: **{h.stage}**",
            f"- Completed: {', '.join(h.completed) or 'none'}",
            f"- Updated: {h.updated_at}",
            "",
            "## Resume",
            "",
            f"Re-run the same command with `--resume {h.run_id}`. Completed stages "
            f"are not re-run, so the provenance record and its hash are preserved.",
        ]
        if h.review_score:
            lines += ["", "## Review", "",
                      f"- Weighted score: {h.review_score.get('weighted')}",
                      f"- Decision: {h.review_score.get('decision')}"]
        if h.refusals:
            lines += ["", "## Refusals", ""] + [f"- {r}" for r in h.refusals]
        memory_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
