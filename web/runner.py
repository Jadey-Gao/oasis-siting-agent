"""The only thing here that touches the analysis, and it does so at arm's length.

`python -m siting.cli` is executed as a subprocess with `--decisions` and `--out`
pointing inside the session directory. Nothing is imported, so nothing in the web
layer can alter a coverage figure, a check, or a score. That guarantee is
enforced by the operating system rather than by discipline.

What this module does not do is as important as what it does. The CLI prints
scores, coverage counts and check results, and they are tempting to parse. It
does not parse them. Both documents compile from `results.json`, which is what
makes it impossible for them to disagree; lifting a number out of stdout would
create a second source for it and forfeit exactly the guarantee this project is
built on. **The log stream is for watching a run happen. Every figure the
interface displays comes from `results.json`.**

Lines come in three shapes, and all three are already stable in `cli.py`:

    [L1] retrieving water data ...      a tagged stage line
         PASS   geometry: all 3 ...     five-space indent, belongs to the line above
    decisions file: no decisions ...    untagged, normally a refusal or an error

An indented line is not an event of its own. It is marked `continues` and
carries the stage it belongs to, so the multi-line refusal report that exit code
4 prints arrives as one block rather than as orphan lines.

Exit codes are the state machine and are passed up untouched:

    0  issued
    2  bad input, or an independent check rejected the plan
    3  below the review floor; nothing issued unless --force-issue
    4  decisions outstanding; the CLI prints which, and why each has no default
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import AsyncIterator

REPO = Path(__file__).resolve().parent.parent

# The pipeline, mirroring siting.harness.Stage.order(). Kept as one constant so
# a progress rail has something to draw and so the mirroring is visible in one
# place rather than implied across the file.
STAGES = ("retrieve", "compile", "solve", "review",
          "evaluate", "score", "render", "issue")

# The stage each tag belongs to. Mirrors the layers in cli.py.
STAGE_BY_TAG = {
    "[L1]": "retrieve",
    "[L2]": "compile",
    "[L3]": "solve",
    "[L4]": "review",
    "[L5]": "evaluate",
    "[L6]": "render",
    "[L7]": "issue",
}

# Tags that are not a stage line but still deserve their own kind, because the
# interface treats them differently: a gate refusal and an outstanding
# checkpoint are not progress, they are things a person has to see.
#
# `[review]` is the scoring reviewer, which is Stage.SCORE. It is a different
# thing from `[L4]`, the stage where a person's overrides are priced, and the
# two share the word "review" only by accident of English. The second value is
# the stage to move to, or None to stay where the run was.
KIND_BY_TAG = {
    "[decisions]": ("decisions", None),
    "[gate]": ("gate", None),
    "[checkpoint]": ("checkpoint", None),
    "[review]": ("review", "score"),
    "[harness]": ("harness", None),
}

INDENT = "     "  # five spaces: cli.py's continuation lines


class Outcome(IntEnum):
    """What the run concluded. The values are the CLI's own exit codes."""

    ISSUED = 0
    HALTED = 2
    BELOW_FLOOR = 3
    DECISIONS_OUTSTANDING = 4
    UNKNOWN = -1

    @classmethod
    def from_code(cls, code: int) -> "Outcome":
        try:
            return cls(code)
        except ValueError:
            return cls.UNKNOWN

    @property
    def issued(self) -> bool:
        return self is Outcome.ISSUED

    def describe(self) -> str:
        return {
            Outcome.ISSUED: "issued",
            Outcome.HALTED: "the inputs were not usable, or a check rejected the plan",
            Outcome.BELOW_FLOOR: "scored below the review floor; nothing was issued",
            Outcome.DECISIONS_OUTSTANDING: "decisions are outstanding; the run did not start",
            Outcome.UNKNOWN: "ended in a way the CLI does not define",
        }[self]


@dataclass(frozen=True)
class RunSpec:
    """One invocation. Everything the CLI needs, and nothing it does not.

    `pricing.py` builds these too, with `mode="auto"` and a partial decisions
    file, to cost one option at a time.
    """

    country: str
    adm2: str
    iso3: str
    out_dir: Path
    decisions: Path | None = None
    overrides: Path | None = None
    domain: str = "water"
    mode: str = "manual"
    budget: int | None = None
    fmt: str = "both"
    reviewer: str = "rules"
    figure_review: Path | None = None
    force_issue: bool = False
    resume: str | None = None  # a run id under out_dir; completed stages are
                                # skipped. Needed to attach a figure_review to
                                # the same run it was reviewed from, rather
                                # than a fresh one — see figure_review's use in
                                # web/claude.py's `_run_assessment`.

    def command(self) -> list[str]:
        # -u matters. Without it Python block-buffers stdout when it is not a
        # terminal, the whole log arrives at once, and the streaming is a
        # pretence. sys.executable rather than "python" so a virtualenv holds.
        cmd = [
            sys.executable, "-u", "-m", "siting.cli",
            "--country", self.country,
            "--adm2", self.adm2,
            "--iso3", self.iso3,
            "--domain", self.domain,
            "--mode", self.mode,
            "--out", str(self.out_dir),
            "--format", self.fmt,
            "--reviewer", self.reviewer,
        ]
        if self.decisions is not None:
            cmd += ["--decisions", str(self.decisions)]
        if self.overrides is not None:
            cmd += ["--overrides", str(self.overrides)]
        if self.budget is not None:
            cmd += ["--budget", str(self.budget)]
        if self.figure_review is not None:
            cmd += ["--figure-review", str(self.figure_review)]
        if self.resume is not None:
            cmd += ["--resume", self.resume]
        if self.force_issue:
            cmd += ["--force-issue"]
        return cmd


@dataclass
class Event:
    """One line of the run, typed just enough to render it."""

    kind: str                      # stage | decisions | gate | checkpoint | review
                                   # | harness | detail | log | done
    text: str                      # the line with its tag stripped
    stage: str | None = None       # where the run is: one of STAGES
    section: str | None = None     # on a detail line, the kind of block it is under
    continues: bool = False        # an indented line belonging to the one before
    raw: str = ""
    data: dict | None = None       # populated on the terminal `done` event only

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "text": self.text, "continues": self.continues}
        if self.stage:
            d["stage"] = self.stage
        if self.section:
            d["section"] = self.section
        if self.data is not None:
            d["data"] = self.data
        return d


def classify(line: str, stage: str | None, section: str | None) -> Event:
    """One line to one event.

    `stage` is where the run had got to, and `section` is the kind of the last
    block opened, so that an indented line is attached to the thing it belongs
    to rather than floating.
    """
    if line.startswith(INDENT) or (line.startswith("  ") and not line.startswith("[")):
        return Event(kind="detail", text=line.strip(), stage=stage,
                     section=section, continues=True, raw=line)

    tag = line.split(" ", 1)[0] if line.startswith("[") else ""
    rest = line[len(tag):].strip() if tag else line

    if tag in STAGE_BY_TAG:
        return Event(kind="stage", text=rest, stage=STAGE_BY_TAG[tag], raw=line)
    if tag in KIND_BY_TAG:
        kind, moves_to = KIND_BY_TAG[tag]
        return Event(kind=kind, text=rest, stage=moves_to or stage, raw=line)
    return Event(kind="log", text=line, stage=stage, raw=line)


def _run_dirs(out_dir: Path) -> set[Path]:
    return {p for p in out_dir.glob("*") if p.is_dir()} if out_dir.exists() else set()


async def stream(spec: RunSpec, transcript: Path | None = None) -> AsyncIterator[Event]:
    """Run one assessment, yielding events as the CLI prints them.

    The final event has `kind == "done"` and carries the outcome and the run
    directory. A directory exists even when nothing was issued: exit code 3
    still writes `results.json`, and the interface has to be able to show the
    scorecard that refused it.
    """
    spec.out_dir.mkdir(parents=True, exist_ok=True)
    before = _run_dirs(spec.out_dir)

    cmd = spec.command()
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(REPO), env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    lines: list[str] = []
    stage: str | None = None
    section: str | None = None
    try:
        assert proc.stdout is not None
        async for chunk in proc.stdout:
            line = chunk.decode("utf-8", errors="replace").rstrip("\n")
            lines.append(line)
            ev = classify(line, stage, section)
            if not ev.continues:
                stage, section = ev.stage, ev.kind
            yield ev

        code = await proc.wait()
    finally:
        # A cancelled consumer — the browser closed the stream, or the turn
        # that called this was itself cancelled — must not leave
        # `python -m siting.cli` running unsupervised. A run ends when its
        # stream closes; that has to be true of the subprocess too.
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    outcome = Outcome.from_code(code)
    if spec.resume is not None:
        # A resumed run reuses its own directory rather than creating a new
        # one, so the "what appeared since we started" heuristic below would
        # see nothing new and report no run_dir at all. The id is already
        # known — this is the one case that doesn't need to be inferred.
        candidate = spec.out_dir / spec.resume
        run_dir = candidate if candidate.is_dir() else None
    else:
        new = _run_dirs(spec.out_dir) - before
        run_dir = max(new, key=lambda p: p.stat().st_mtime) if new else None

    if transcript is not None:
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

    yield Event(
        kind="done",
        text=outcome.describe(),
        data={
            "outcome": outcome.name.lower(),
            "exit_code": int(code),
            "issued": outcome.issued,
            "run_dir": str(run_dir) if run_dir else None,
            "command": " ".join(cmd),
        },
    )


def results(run_dir: Path) -> dict:
    """The run's own account. The only source for any figure the page shows."""
    return json.loads((run_dir / "results.json").read_text(encoding="utf-8"))


def documents(run_dir: Path) -> dict[str, Path]:
    """Whatever the run compiled, by format name."""
    out = {}
    for name, f in (("bundle", "evidence-bundle.pdf"), ("assessment", "assessment.pdf")):
        p = run_dir / f
        if p.exists():
            out[name] = p
    return out


async def _demo(argv: list[str]) -> int:
    """`python -m web.runner <country> <adm2> <iso3> [decisions.yaml]`

    Here so the module can be exercised before anything else in web/ exists.
    """
    if len(argv) < 3:
        print(_demo.__doc__)
        return 2
    country, adm2, iso3 = argv[0], argv[1], argv[2]
    spec = RunSpec(
        country=country, adm2=adm2, iso3=iso3,
        out_dir=REPO / "sessions" / "_demo" / "runs",
        decisions=Path(argv[3]).resolve() if len(argv) > 3 else None,
        mode="manual" if len(argv) > 3 else "auto",
        budget=None if len(argv) > 3 else 3,
    )

    done: Event | None = None
    async for ev in stream(spec, transcript=REPO / "sessions" / "_demo" / "last_run.log"):
        if ev.continues:
            print(f"{'':<10}   {ev.text}")
        else:
            label = ev.stage if ev.kind == "stage" else ev.kind
            print(f"{label:<10} {ev.text}")
        if ev.kind == "done":
            done = ev

    assert done and done.data
    print()
    print(f"exit {done.data['exit_code']}  ->  {done.data['outcome']}")
    print(f"run dir: {done.data['run_dir']}")
    if done.data["run_dir"]:
        doc = results(Path(done.data["run_dir"]))
        p = doc["plan"]
        print(f"plan: {len(p['sites'])} sites, {p['newly_covered']:,} newly covered")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_demo(sys.argv[1:])))
