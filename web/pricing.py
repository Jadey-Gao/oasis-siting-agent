"""What each option would cost, measured rather than described.

An officer choosing a service radius should see the consequence before choosing,
not after. These numbers are produced the only honest way available: by running
the real analysis once per option and reading the answer out of `results.json`.

No new analysis code is needed. A partial decisions file carrying only the key
under test, run with `--mode auto`, lets the agent fill the rest from
`decisions.AUTO_BASIS`; the probe's own results then carry the figure.

Three things must stay true of every number this module returns.

- **A probe is not a plan.** Every decision in it except the one under test was
  taken by the agent, and its `authorship` statement is carried through and
  shown. A probe's figures are never presented as the district's position.
- **Nothing is computed here.** Each figure is a field in a probe's
  `results.json`. Where a comparison is wanted, it is a subtraction between two
  such fields, and both are shown.
- **Not everything can be priced, and the ones that cannot are named.** Budget
  comes from a capital programme; whether an eighteen-year-old register is fit
  to direct spending is a judgement about evidence, not a quantity. Silently
  omitting them would suggest they are lesser decisions. They are not.

Probes are slow enough to matter (about four seconds each, seven for a full
district) and their answers never change, so `prewarm.py` runs them at build
time into `sessions/_pricing/` and the interview reads the cache.

    python -m web.pricing Tanzania Ngara TZA 3
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from siting import decisions as dec

from . import emit, runner
from .runner import REPO, RunSpec

CACHE = REPO / "sessions" / "_pricing"

# The options each decision is priced at. Taken from the register's own
# `options_hint`, which is prose, so the values are restated here as data — the
# one place that happens, and a divergence from the hint is a bug in this line.
OPTIONS: dict[str, tuple[Any, ...]] = {
    "service_radius_m": (500.0, 1000.0, 1500.0),
    "coverage_basis": ("straight_line", "walking_time"),
    "objective": ("max_coverage", "worst_case"),
}

# Named rather than omitted. An officer should be able to see that the system
# tried and why it stopped, not infer from a blank space that the decision is
# unimportant.
NOT_PRICEABLE: dict[str, str] = {
    "budget": "This comes from a capital programme. There is nothing in the data "
              "to infer it from, and every probe below is run at the budget you "
              "give, so it has to be settled first.",
    "data_currency_accepted": "This is a judgement about whether evidence of a "
              "given age is fit to direct capital spending. The scout reports the "
              "register's median age; there is no quantity to trade off against it.",
    "coverage_tolerance": "Not probed. It is arithmetic on a plan that already "
              "exists: see `tolerance_in_people`, which states what each threshold "
              "would mean in people on this district's own numbers.",
    "equity_accepted": "Only becomes a real question once a plan exists to ask it "
              "about. The evidence is the equity check on that plan.",
    "review_floor": "This is a threshold on the review score, not a coverage "
              "parameter. There is no district data to price it against; "
              "`--force-issue` already lets a district cross whatever line it sets.",
}


@dataclass(frozen=True)
class Priced:
    """One option, with what it did to this district's numbers."""

    value: Any
    label: str
    covered_today: int          # who the assessment would say already has service
    covered_share: float
    newly_covered: int          # what the programme would add at this setting
    evidence: dict[str, Any]    # key-specific, each field straight from results.json
    attribution: str            # who took the other decisions in this probe
    run_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Table:
    """Every option for one decision, priced on one district's data."""

    key: str
    question: str
    why_not_a_default: str
    unit: str
    options_hint: str
    priced: tuple[Priced, ...] = ()
    unpriceable: str | None = None
    degenerate: str | None = None   # set when two options are not actually a choice
    caveat: str = (
        "These are probe runs. In each one, every decision except the one being "
        "compared was taken by the agent, so the figures show what this choice "
        "changes — they are not this district's plan."
    )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["priced"] = [p.to_dict() for p in self.priced]
        return d


def _spec(key: str, value: Any, base: dict[str, Any], budget: int,
          work: Path) -> RunSpec:
    """A probe: one decision recorded, the rest left to `--mode auto`."""
    emit.write_decisions(
        work / "decisions.yaml",
        [emit.Answer(
            key=key, value=value, decided_by="pricing probe",
            reason=f"Recorded alone so that {key} can be compared at {value!r}. "
                   "This is a probe, not a district position.",
        )],
        mode="auto", district=base["adm2"],
    )
    return RunSpec(
        country=base["country"], adm2=base["adm2"], iso3=base["iso3"],
        domain=base.get("domain", "water"),
        out_dir=work / "runs", decisions=work / "decisions.yaml",
        mode="auto", budget=budget,
        fmt="bundle",   # at most one document, and usually none: a probe that
                        # scores below the floor never reaches the renderer
    )


def _evidence(key: str, doc: dict[str, Any]) -> dict[str, Any]:
    """What this particular choice turns on, read from the probe's own account."""
    if key == "objective":
        # No worst-served distance is recorded in results.json, so it is not
        # claimed. What is recorded is how far each chosen site sits from the
        # nearest working point, and the evaluator's own equity finding.
        d = [s["nearest_working_m"] for s in doc["plan"]["sites"]
             if s.get("nearest_working_m") is not None]
        ev = {}
        if d:
            ev["nearest_working_m_median"] = int(statistics.median(d))
            ev["nearest_working_m_max"] = int(max(d))
        equity = next((e for e in doc["evaluation"] if e["check"] == "equity"), None)
        if equity:
            ev["equity"] = equity["detail"]
            ev["equity_level"] = equity["level"]
        return ev

    if key == "coverage_basis":
        s = doc["scope"]
        ev = {"rule": s.get("coverage_rule", "")}
        for f in ("coverage_minutes", "friction_calibration"):
            if s.get(f) is not None:
                ev[f] = s[f]
        return ev

    if key == "service_radius_m":
        return {"uncovered": doc["baseline"]["uncovered"],
                "rule": doc["scope"].get("coverage_rule", "")}
    return {}


async def _probe(key: str, value: Any, base: dict[str, Any], budget: int) -> Priced:
    work = CACHE / "work" / f"{base['iso3']}-{base['adm2']}-{key}-{value}".lower().replace(" ", "-")
    spec = _spec(key, value, base, budget, work)

    done = None
    async for ev in runner.stream(spec):
        if ev.kind == "done":
            done = ev
    assert done and done.data
    if not done.data["run_dir"]:
        raise RuntimeError(
            f"probing {key}={value!r} produced nothing: the run exited "
            f"{done.data['exit_code']} ({done.data['outcome']})"
        )

    doc = runner.results(Path(done.data["run_dir"]))
    return Priced(
        value=value,
        label=_label(key, value),
        covered_today=doc["baseline"]["covered"],
        covered_share=doc["baseline"]["covered_share"],
        newly_covered=doc["plan"]["newly_covered"],
        evidence=_evidence(key, doc),
        attribution=doc["authorship"]["statement"],
        run_id=doc["run"]["id"],
    )


def _degeneracy(priced: list[Priced]) -> str | None:
    """Two options that produce the same answer are not a choice.

    An option table exists to show a trade. If two settings return identical
    figures on this district's data, presenting them side by side invites an
    officer to deliberate over a difference that does not exist. It is also how
    a solver that silently falls back to another objective would look — which is
    exactly what a half-written `p_centre` looked like while this was being
    tested, and why the check is here rather than in a comment.
    """
    seen: dict[tuple, list[str]] = {}
    for p in priced:
        seen.setdefault((p.covered_today, p.newly_covered), []).append(p.label)
    same = [labels for labels in seen.values() if len(labels) > 1]
    if not same:
        return None
    pairs = "; ".join(" and ".join(g) for g in same)
    return (f"{pairs} produce identical figures on this district's data. Either "
            "the choice does not bite here, or the two settings are not in fact "
            "being applied differently. Do not present this as a trade-off "
            "without saying which.")


def _label(key: str, value: Any) -> str:
    if key == "service_radius_m":
        return f"{int(value)} m"
    return str(value).replace("_", " ")


def _spec_of(key: str) -> dec.Required:
    for r in dec.REGISTER + dec.DEFERRED:
        if r.key == key:
            return r
    raise KeyError(f"{key} is not a decision the register declares")


def _cache_file(base: dict[str, Any], budget: int) -> Path:
    slug = f"{base['iso3']}-{base['adm2']}-{base.get('domain', 'water')}-b{budget}"
    return CACHE / f"{slug.lower().replace(' ', '-')}.json"


async def price(base: dict[str, Any], budget: int, keys: tuple[str, ...] | None = None,
                *, refresh: bool = False, cache: bool = True) -> dict[str, Table]:
    """Price every priceable decision for one district at one budget.

    `base` is `{country, adm2, iso3, domain}`. Results are cached to disk, since
    a probe's answer does not change and an officer should not wait for it. The
    interview prices one decision at a time, so the cache file accumulates one
    entry per key across calls rather than holding only whichever key was priced
    most recently — a call for `objective` must not make an earlier, separately
    cached `service_radius_m` table disappear, and must not be answered out of a
    cache that was only ever asked for `service_radius_m`.

    `refresh` re-runs the probes for the keys being priced now and replaces
    their entries in the cache; it does not disable the cache for other keys,
    and does not discard entries this call was not asked about.
    """
    cache_file = _cache_file(base, budget)
    wanted = keys or tuple(OPTIONS)

    tables: dict[str, Table] = {}
    if cache and cache_file.exists():
        raw = json.loads(cache_file.read_text(encoding="utf-8"))
        tables = {k: Table(**{**v, "priced": tuple(Priced(**p) for p in v["priced"])})
                  for k, v in raw["tables"].items()}

    todo = [k for k in wanted if refresh or k not in tables]
    for key in todo:
        spec = _spec_of(key)
        if key in NOT_PRICEABLE:
            tables[key] = Table(key=key, question=spec.question,
                                why_not_a_default=spec.why_not_a_default,
                                unit=spec.unit, options_hint=spec.options_hint,
                                unpriceable=NOT_PRICEABLE[key])
            continue
        priced = [await _probe(key, v, base, budget) for v in OPTIONS[key]]
        tables[key] = Table(key=key, question=spec.question,
                            why_not_a_default=spec.why_not_a_default,
                            unit=spec.unit, options_hint=spec.options_hint,
                            priced=tuple(priced), degenerate=_degeneracy(priced))

    if cache and todo:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(
            {"scope": base, "budget": budget,
             "tables": {k: t.to_dict() for k, t in tables.items()}},
            indent=2, ensure_ascii=False), encoding="utf-8")
    return {k: tables[k] for k in wanted}


def unpriceable_tables() -> dict[str, Table]:
    """The decisions that carry no probe, with the reason stated."""
    out = {}
    for key, why in NOT_PRICEABLE.items():
        spec = _spec_of(key)
        out[key] = Table(key=key, question=spec.question,
                         why_not_a_default=spec.why_not_a_default,
                         unit=spec.unit, options_hint=spec.options_hint,
                         unpriceable=why)
    return out


def tolerance_in_people(doc: dict[str, Any],
                        options: tuple[float, ...] = (0.02, 0.05, 0.10)
                        ) -> list[dict[str, Any]]:
    """What each escalation threshold would mean, on this run's own numbers.

    The guardrail divides people forgone by total covered, which includes those
    already served. On a district where the plan's whole gain is smaller than
    the threshold, no override can breach it, and the officer should be told
    that before choosing rather than discovering it in the report.
    """
    covered = doc["plan"]["covered"]
    gain = doc["plan"]["newly_covered"]
    out = []
    for t in options:
        people = int(round(t * covered))
        out.append({
            "value": t,
            "label": f"{t:.0%}",
            "people": people,
            "inert": people >= gain,
            "note": (f"{people:,} people, which is more than this plan's entire gain "
                     f"of {gain:,}. At this threshold no override could breach it, "
                     "so a passing verdict would carry no information."
                     if people >= gain else
                     f"{people:,} people of the {covered:,} this plan covers."),
        })
    return out


def _main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: python -m web.pricing <country> <adm2> <iso3> <budget> [--fresh]")
        return 2
    base = {"country": argv[0], "adm2": argv[1], "iso3": argv[2], "domain": "water"}
    budget = int(argv[3])
    tables = asyncio.run(price(base, budget, refresh="--fresh" in argv))

    for key, t in tables.items():
        print(f"\n{t.question}")
        print(f"  ({key}{', ' + t.unit if t.unit else ''})")
        if t.unpriceable:
            print(f"  not priced: {t.unpriceable}")
            continue
        if t.degenerate:
            print(f"  !! {t.degenerate}")
        for p in t.priced:
            print(f"    {p.label:<14} 现状覆盖 {p.covered_today:>8,} ({p.covered_share:>5.1%})"
                  f"   方案新增 {p.newly_covered:>7,}")
            for k, v in p.evidence.items():
                if isinstance(v, (int, float, str)) and len(str(v)) < 140:
                    print(f"      {k}: {v}")
    cache_file = _cache_file(base, budget)
    print(f"\n{'cached to' if cache_file.exists() else 'NOT cached; expected'} {cache_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
