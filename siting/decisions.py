"""Decisions that belong to a person, and the refusal to make them for them.

An earlier version of this system carried a 1,000 m service radius, a ten year
staleness threshold, a five per cent coverage tolerance and a review floor of
6.5 as constants in the source. Every one of those is a judgement about what
counts as served, what counts as current, and how much coverage a district is
willing to forgo. None of them is a technical parameter, and writing them into
the code moved the decision from the officer accountable for it to whoever
wrote the file.

All four are now questions in the register below. Two of them left numbers
behind in the evaluator, and those numbers are no longer decisions: the ten
years in `evaluate.REPORT_AGE_ABOVE_YEARS` and the fifteen points in
`evaluate.DENSITY_GAP_REPORTING_THRESHOLD` decide only when a finding draws
attention to itself. Whether a register of that age may direct spending, and
whether the resulting distribution is acceptable, are answered here.

A decision that is a position rather than a quantity carries one of its declared
`choices` and nothing else. The officer's conditions and reasoning go in
`reason`, which is printed beside the value wherever the value appears. Reading
a position out of a sentence would put the machine back in the business of
deciding what the officer meant, which is the one thing this module exists to
prevent.

This module keeps a register of such decisions. Nothing downstream reads a
default: a run without a recorded decision on each of them refuses to proceed
and reports what has to be settled. The agent's job is to prepare the choice,
with evidence and options; the choice itself is not its to make.

Technical parameters are a different matter and are deliberately not here. A
lattice spacing or a raster aggregation factor has a defensible right answer
given the radius, and asking a district officer to pick one wastes the only
attention the process actually has.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Required:
    """One decision that must be made by a person before a run can proceed."""
    key: str
    question: str
    why_not_a_default: str
    unit: str = ""
    options_hint: str = ""
    # Where the decision is a position rather than a quantity, the position is
    # one of these and nothing else. Empty means the value is unconstrained.
    choices: tuple[str, ...] = ()


REGISTER: tuple[Required, ...] = (
    Required(
        key="service_radius_m",
        question="What distance, or what walking time, counts as served?",
        why_not_a_default="This defines who the assessment says already has service "
                          "and who does not, so it sets the size of the gap it reports. "
                          "A 1 km walk is a common stand-in for the 30 minutes the JMP "
                          "uses for a basic service, but terrain, season and who does "
                          "the walking all bear on it, and none of that is in the data.",
        unit="metres",
        options_hint="500 (strict) | 1000 (JMP-aligned) | 1500 (permissive)",
    ),
    Required(
        key="coverage_basis",
        question="Should reach be measured as straight-line distance, or as walking "
                 "time over terrain?",
        why_not_a_default="Straight-line distance ignores rivers, ridges and the "
                          "absence of a path, and so overstates who is served. Walking "
                          "time over a published friction surface is closer to what a "
                          "household experiences, but the surface is modelled at about "
                          "one kilometre and does not know the footpaths a village "
                          "actually uses. Which error a district prefers to carry is "
                          "its judgement.",
        options_hint="straight_line | walking_time",
    ),
    Required(
        key="objective",
        question="Should the programme reach the most people, or the worst-served people?",
        why_not_a_default="These are different objectives with different answers. "
                          "Maximum coverage reaches the largest number and concentrates "
                          "on settled areas; minimising the worst case reaches remote "
                          "households at a lower total. Choosing between them is a "
                          "distributional judgement, not an optimisation detail.",
        options_hint="max_coverage (greedy MCLP, within 1-1/e of the optimum) | "
                     "worst_case (population-weighted p-centre)",
    ),
    Required(
        key="budget",
        question="How many facilities does the programme fund?",
        why_not_a_default="This comes from a capital programme, not from the data.",
        unit="facilities",
    ),
    Required(
        key="data_currency_accepted",
        question="Is the age of the source register acceptable for this decision?",
        why_not_a_default="The register's median record age is reported before this is "
                          "asked. Whether a survey of that age is fit to direct capital "
                          "spending is a judgement for the officer who will answer for "
                          "the spending, and it cannot be settled by a threshold in code.",
        options_hint="yes | no | unresolved   (conditions belong in `reason`)",
        choices=("yes", "no", "unresolved"),
    ),
    Required(
        key="coverage_tolerance",
        question="How much coverage may reviewing decisions forgo before that is "
                 "escalated in the report?",
        why_not_a_default="A veto on a recommended site costs coverage. Where the line "
                          "sits between an acceptable local judgement and a loss that "
                          "should be flagged upward is a governance question.",
        unit="share of achievable coverage",
        options_hint="0.02 (tight) | 0.05 | 0.10 (loose)",
    ),
    Required(
        key="review_floor",
        question="How complete must the account be before the assessment may be "
                 "issued without an explicit instruction?",
        why_not_a_default="The scoring reviewer weighs data adequacy, method fitness, "
                          "spatial rigour, accountability and actionability. Where the "
                          "line sits below which an account is not fit to act on is a "
                          "judgement about how much risk a district will carry, not a "
                          "property of the scoring. A district willing to act on a "
                          "thinner account than its neighbour is making a defensible "
                          "choice, and --force-issue already lets it cross the line, "
                          "which is the clearest sign the line was always its own.",
        unit="weighted score out of 10",
        options_hint="6.5 (the value this system used to hard-code) | 5.0 (permissive) "
                     "| 8.0 (strict)",
    ),
)

REQUIRED_KEYS = {r.key for r in REGISTER}


# What the agent will choose in automatic mode, and the basis it will record.
# These are not defaults in the ordinary sense: nothing reads them unless the
# operator has asked for automatic mode, and every one that is used is attributed
# to the agent in the output, so a reader can always see which decisions a person
# made and which the machine made in their absence.
AUTO_BASIS: dict[str, tuple[object, str]] = {
    "coverage_basis": (
        "walking_time",
        "Walking time over the Malaria Atlas Project friction surface is closer to "
        "what a household on foot experiences than straight-line distance, and is "
        "the basis the agent adopts when no district position has been recorded. "
        "The surface is modelled at roughly one kilometre and does not represent "
        "local footpaths, so it understates the reach of a village that has one "
        "and overstates it where a river intervenes.",
    ),
    "service_radius_m": (
        1000.0,
        "1 km stands in for the 30 minutes the WHO/UNICEF Joint Monitoring "
        "Programme uses as a basic drinking-water service threshold. Chosen by "
        "the agent in the absence of a district position; the assessment reports "
        "the gap at this radius and the reader should read it as conditional on it.",
    ),
    "objective": (
        "max_coverage",
        "Maximum coverage is the conventional reading of a fixed capital "
        "programme, and is the objective the agent will assume when no "
        "distributional position has been recorded. It favours settled areas by "
        "construction; the equity measurement in the checks reports by how much.",
    ),
    "coverage_tolerance": (
        0.05,
        "Five per cent of achievable coverage is the agent's escalation threshold "
        "in the absence of a governance position. It determines only when a "
        "reviewing decision is flagged upward, never whether it is permitted.",
    ),
    "data_currency_accepted": (
        "unresolved",
        "The agent does not accept or reject the currency of the register on a "
        "district's behalf. In automatic mode it proceeds and states the median "
        "record age prominently, so that the finding is read as describing the "
        "surveyed state of the network rather than its present state.",
    ),
    "equity_accepted": (
        "unresolved",
        "The measured distribution between dense and remote population is "
        "reported without being accepted. Automatic mode does not take a policy "
        "position on it.",
    ),
    "review_floor": (
        6.5,
        "6.5 is the threshold this system carried as a constant before the "
        "decision was moved to the register, retained as the agent's value in the "
        "absence of a district position. It governs only whether the assessment "
        "is issued without an explicit instruction, never whether its findings "
        "are true.",
    ),
}

# Settled after the plan exists, because the question only becomes real then.
DEFERRED: tuple[Required, ...] = (
    Required(
        key="equity_accepted",
        question="The plan's distribution between dense and remote population has been "
                 "measured. Is it accepted?",
        why_not_a_default="Coverage maximisation favours density by construction. "
                          "Whether the resulting distribution is acceptable is a policy "
                          "position, and the assessment records which position was taken.",
        options_hint="yes | no | unresolved   (no means re-run with worst_case)",
        choices=("yes", "no", "unresolved"),
    ),
)

DEFERRED_KEYS = {r.key for r in DEFERRED}


@dataclass
class Decision:
    key: str
    value: Any
    decided_by: str
    reason: str
    authored_by_agent: bool = False
    ts: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%SZ"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalise(value: Any, spec: "Required | None") -> Any:
    """A position decision carries one of its declared choices and nothing else.

    YAML 1.1 turns an unquoted `yes` into the boolean True, so those are folded
    back first. Everything else is checked, never interpreted. Reading a position
    out of a sentence would put the machine back in the business of deciding what
    the officer meant; the officer's conditions live in `reason`, which is printed
    beside the value wherever the value appears.
    """
    if spec is None or not spec.choices:
        return value
    if isinstance(value, bool):
        value = "yes" if value else "no"
    v = str(value).strip().lower()
    if v not in spec.choices:
        raise ValueError(
            f"decision {spec.key!r} records {value!r}. This decision is a position, "
            f"and it carries one of: {' | '.join(spec.choices)}.\n"
            f"    Conditions, caveats and reasoning belong in `reason`, which is "
            f"printed beside the value everywhere the value appears. For example:\n"
            f"        value: yes\n"
            f"        reason: >-\n"
            f"          Accepted on the condition that the assessment states the "
            f"register's age prominently.")
    return v


def _spec_for(key: str) -> "Required | None":
    return next((r for r in REGISTER + DEFERRED if r.key == key), None)


class Missing(Exception):
    """Raised when a run would have to invent a decision to continue."""

    def __init__(self, pending: list[Required], mode: str = "manual") -> None:
        self.pending = pending
        self.mode = mode
        super().__init__(f"{len(pending)} decisions have not been made")

    def report(self) -> str:
        if self.mode == "auto":
            head = [
                "This run cannot proceed even in automatic mode. The decisions below "
                "have no defensible automatic basis: they come from outside the data "
                "and the agent has nothing to infer them from.",
                "",
            ]
        else:
            head = [
                "This run is in manual mode and cannot proceed. The following are "
                "decisions for a person. Record them, or re-run with --mode auto, in "
                "which case the agent decides and every such decision is attributed to "
                "the agent in the output.",
                "",
            ]
        lines = list(head)
        for r in self.pending:
            lines.append(f"  {r.key}")
            lines.append(f"    {r.question}")
            if r.options_hint:
                lines.append(f"    options: {r.options_hint}"
                             + (f"  ({r.unit})" if r.unit else ""))
            lines.append(f"    why this is not a default: {r.why_not_a_default}")
            lines.append("")
        lines += [
            "Record each in a decisions file and pass it with --decisions:",
            "",
            "  decisions:",
            "    - key: service_radius_m",
            "      value: 1000",
            "      decided_by: District Water Officer",
            "      reason: >-",
            "        Aligns with the JMP basic-service threshold used in the district's",
            "        own reporting.",
        ]
        return "\n".join(lines)


class Register:
    """The decisions made for one run, and the questions still outstanding."""

    def __init__(self, decisions: list[Decision] | None = None,
                 mode: str = "manual") -> None:
        if mode not in ("manual", "auto"):
            raise ValueError(f"mode must be manual or auto, not {mode!r}")
        self._d: dict[str, Decision] = {d.key: d for d in (decisions or [])}
        self.mode = mode

    # --- loading ----------------------------------------------------------- #

    @classmethod
    def load(cls, path: str | Path | None, mode: str = "manual") -> "Register":
        if path is None:
            return cls(mode=mode)
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"no decisions file at {p}")
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        items = raw.get("decisions", raw if isinstance(raw, list) else [])
        # A decision was made when it was written down, not when this run happened
        # to start. Where the file records no timestamp of its own, the file's
        # modification time is the closest honest answer available. Stamping "now"
        # claimed the officer decided at the instant the analysis ran, and gave the
        # same decision a different date on every resume.
        file_ts = dt.datetime.fromtimestamp(
            p.stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for it in items:
            for f in ("key", "value", "decided_by", "reason"):
                if f not in it or (isinstance(it[f], str) and not it[f].strip()):
                    raise ValueError(f"decision {it.get('key', '?')!r} is missing {f}")
            out.append(Decision(key=it["key"],
                                value=_normalise(it["value"], _spec_for(it["key"])),
                                decided_by=it["decided_by"], reason=it["reason"],
                                ts=it.get("ts") or file_ts))
        return cls(out, mode=mode)

    # --- access ------------------------------------------------------------ #

    def require(self, *keys: str) -> None:
        """Settle every named decision, or refuse to continue.

        In manual mode an unrecorded decision stops the run. In automatic mode
        the agent takes the decision itself, records the basis on which it did
        so, and attributes it to itself, so that the distinction between a
        district's position and a machine's assumption survives into the report.
        A decision with no defensible automatic basis stops the run in either
        mode: `budget` comes from a capital programme and cannot be inferred.
        """
        wanted = set(keys) or REQUIRED_KEYS
        pending = [r for r in REGISTER + DEFERRED
                   if r.key in wanted and r.key not in self._d]
        if not pending:
            return

        if self.mode == "auto":
            still: list[Required] = []
            for r in pending:
                if r.key in AUTO_BASIS:
                    value, basis = AUTO_BASIS[r.key]
                    self.record(r.key, value, "agent (automatic mode)", basis,
                                authored_by_agent=True)
                else:
                    still.append(r)
            pending = still

        if pending:
            raise Missing(pending, mode=self.mode)

    def get(self, key: str) -> Any:
        if key not in self._d:
            spec = next((r for r in REGISTER + DEFERRED if r.key == key), None)
            raise Missing([spec] if spec else [])
        return self._d[key].value

    def has(self, key: str) -> bool:
        return key in self._d

    def outstanding(self, include_deferred: bool = False) -> list[Required]:
        pool = REGISTER + (DEFERRED if include_deferred else ())
        return [r for r in pool if r.key not in self._d]

    def record(self, key: str, value: Any, decided_by: str, reason: str,
               authored_by_agent: bool = False) -> Decision:
        # The agent is held to the same rule as a person: a position decision it
        # takes in automatic mode is one of the declared choices, not a sentence.
        value = _normalise(value, _spec_for(key))
        d = Decision(key=key, value=value, decided_by=decided_by, reason=reason,
                     authored_by_agent=authored_by_agent)
        self._d[key] = d
        return d

    @property
    def by_agent(self) -> list[Decision]:
        return [d for d in self._d.values() if d.authored_by_agent]

    @property
    def by_person(self) -> list[Decision]:
        return [d for d in self._d.values() if not d.authored_by_agent]

    def to_list(self) -> list[dict[str, Any]]:
        order = [r.key for r in REGISTER + DEFERRED]
        return [self._d[k].to_dict() for k in order if k in self._d]

    def spec(self, key: str) -> Required | None:
        return _spec_for(key)

    def stance(self, key: str) -> str:
        """The position a decision records, or "unresolved" if it was never made.

        No interpretation: the value is already one of the declared choices,
        because `_normalise` refused the run otherwise.
        """
        return str(self._d[key].value) if key in self._d else "unresolved"

    def annotated(self) -> list[dict[str, Any]]:
        """Decisions with the question each one answers, for the report."""
        out = []
        for d in self.to_list():
            s = self.spec(d["key"])
            out.append({**d, "question": s.question if s else "",
                        "unit": s.unit if s else "",
                        # Empty where the decision is a quantity rather than a
                        # position, so a reader is never shown a stance that the
                        # decision does not actually express.
                        "stance": str(d["value"]) if (s and s.choices) else ""})
        return out

    def summary(self) -> dict[str, Any]:
        """What a reader needs in order to know who decided what."""
        agent, person = self.by_agent, self.by_person
        return {
            "mode": self.mode,
            "by_person": len(person),
            "by_agent": len(agent),
            "agent_authored_keys": [d.key for d in agent],
            "statement": (
                f"All {len(person)} decisions in this run were recorded by a person."
                if not agent else
                f"{len(person)} of {len(person) + len(agent)} decisions were recorded "
                f"by a person. The remaining {len(agent)} were taken by the agent in "
                f"automatic mode and are attributed to it: "
                f"{', '.join(d.key for d in agent)}."
            ),
        }

    def __len__(self) -> int:
        return len(self._d)
