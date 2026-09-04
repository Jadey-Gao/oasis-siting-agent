"""L4 planner overrides.

A planner gets exactly four verbs. Free text is translated into one of them and
nothing else, so a language model never proposes an allocation: research on
algorithm-in-the-loop decision making (Green and Chen, CSCW 2019 and 2021) shows
people reweight their own judgement once a machine recommendation is in front of
them, so the model is kept out of the generate role entirely.

Overrides live in a YAML file under version control rather than in a UI, which
makes a planner's decisions a reviewable artefact instead of an ephemeral click.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml

from .compile import Instance
from .solve import Solution, site_ids, solve as solve_instance

Verb = Literal["VETO", "PIN", "REWEIGHT", "RESCOPE"]
VERBS: tuple[str, ...] = ("VETO", "PIN", "REWEIGHT", "RESCOPE")


@dataclass
class Override:
    verb: Verb
    reason: str
    actor: str = "planner"
    site: str | None = None          # site id from a previous run, e.g. "S-004"
    candidate: int | None = None     # or a raw candidate index
    # A planner who says "not there" means a place, not a lattice point. Vetoing a
    # single candidate is nearly free when the next candidate is 750 m away.
    veto_radius_m: float = 500.0
    equity_weight: float | None = None
    radius_m: float | None = None
    budget: int | None = None
    ts: str = ""

    def __post_init__(self) -> None:
        if self.verb not in VERBS:
            raise ValueError(f"{self.verb!r} is not one of {VERBS}")
        if not self.reason or not self.reason.strip():
            raise ValueError(f"{self.verb} without a reason is not accepted")
        if not self.ts:
            self.ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(path: str | Path) -> list[Override]:
    p = Path(path)
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    return [Override(**item) for item in raw]


def _within(inst: Instance, idx: int, radius_m: float) -> list[int]:
    """Candidates inside the vetoed area, measured in the projected CRS."""
    lat = inst.candidates["lat"].to_numpy(float)
    lon = inst.candidates["lon"].to_numpy(float)
    if inst.projection is not None:
        from .spatial import to_projected
        x, y = to_projected(lat, lon, inst.projection)
        d2 = (x - x[idx]) ** 2 + (y - y[idx]) ** 2
        return [int(i) for i in np.where(d2 <= radius_m ** 2)[0]]
    from .clean import haversine_m
    d = haversine_m(lat[idx], lon[idx], lat, lon)
    return [int(i) for i in np.where(d <= radius_m)[0]]


def apply(inst: Instance, overrides: list[Override]) -> Instance:
    """Fold overrides into the instance. RESCOPE of the radius is not applied here
    because it changes the coverage matrix; the caller rebuilds in that case."""
    for o in overrides:
        if o.verb == "VETO" and o.candidate is not None:
            inst.must_exclude.extend(_within(inst, int(o.candidate), o.veto_radius_m))
        elif o.verb == "PIN" and o.candidate is not None:
            inst.must_include.append(int(o.candidate))
        elif o.verb == "RESCOPE" and o.budget is not None:
            inst.budget = int(o.budget)
        elif o.verb == "REWEIGHT" and o.equity_weight is not None:
            if inst.objective != "worst_case":
                raise ValueError(
                    f"REWEIGHT changes how much population size counts when the "
                    f"worst_case objective chooses who is worst-served, and this "
                    f"run's objective is {inst.objective!r}, which has no such "
                    f"balance to shift. Re-run with objective=worst_case, or use "
                    f"RESCOPE/VETO/PIN instead.")
            if not 0.0 <= o.equity_weight <= 1.0:
                raise ValueError(
                    f"equity_weight must be between 0 (worst-off cell wins "
                    f"regardless of size) and 1 (weighted equally by population), "
                    f"got {o.equity_weight!r}")
            inst.equity_exponent = float(o.equity_weight)
    return inst


def needs_rebuild(overrides: list[Override]) -> float | None:
    """Return the new radius if any RESCOPE changes it, else None."""
    for o in reversed(overrides):
        if o.verb == "RESCOPE" and o.radius_m:
            return float(o.radius_m)
    return None


@dataclass
class Diff:
    """What one override cost, in people rather than in percentage points."""
    verb: str
    reason: str
    actor: str
    ts: str
    target: str
    covered_before: int
    covered_after: int
    delta_covered: int
    share_before: float
    share_after: float
    sites_changed: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def price(
    inst: Instance,
    reference: Solution,
    overrides: list[Override],
    site_id: dict[int, str] | None = None,
) -> tuple[Solution, list[Diff], Instance]:
    """Apply overrides one at a time, re-solving after each, so every single
    decision carries its own coverage cost rather than one lumped number.

    Re-solving uses the objective the instance carries, not maximum coverage. An
    officer who chose to reach the worst-served population does not stop having
    chosen it the moment they exercise a veto.

    The instance the overrides were applied to is returned alongside the solution,
    because a RESCOPE changes the budget and the checks and the report have to see
    the same budget the plan was built to.
    """
    site_id = site_id or {}
    diffs: list[Diff] = []

    running = inst.variant()
    current = reference

    for o in overrides:
        # Resolved against `current` — the plan as of right before this
        # override — not the run's original optimum. An officer's second
        # override refers to the site labels the *first* override's plan
        # showed them, not the labels an unconstrained re-solve would produce.
        if o.candidate is None and o.site:
            current_ids = {v: k for k, v in site_ids(current).items()}
            if o.site not in current_ids:
                raise KeyError(f"override refers to unknown site {o.site!r}")
            o.candidate = current_ids[o.site]

        before_sites = set(current.sites)
        before = current.covered(running)
        before_share = current.share(running)

        apply(running, [o])
        current = solve_instance(running)

        after = current.covered(running)
        changed = sorted(
            site_id.get(i, f"c{i}")
            for i in (before_sites ^ set(current.sites))
        )
        diffs.append(Diff(
            verb=o.verb,
            reason=o.reason,
            actor=o.actor,
            ts=o.ts,
            target=o.site or (f"candidate {o.candidate}" if o.candidate is not None else "run scope"),
            covered_before=round(before),
            covered_after=round(after),
            delta_covered=round(after - before),
            share_before=round(before_share, 4),
            share_after=round(current.share(running), 4),
            sites_changed=changed,
        ))

    return current, diffs, running


def guardrail(
    optimum: Solution, final: Solution, inst: Instance, tolerance: float = 0.05
) -> dict[str, Any]:
    """Price the whole override set against the unconstrained optimum.

    This warns; it never blocks. The planner has the final word. The system's job
    is to make sure the cost of that word is written down.
    """
    opt = optimum.covered(inst)
    got = final.covered(inst)
    loss = opt - got
    rel = (loss / opt) if opt else 0.0
    return {
        "optimum_covered": round(opt),
        "final_covered": round(got),
        "people_forgone": round(loss),
        "relative_loss": round(rel, 4),
        "tolerance": tolerance,
        "breached": bool(rel > tolerance),
        "verdict": (
            f"Overrides forgo {round(loss):,} people, {rel:.1%} of the achievable "
            f"coverage, which exceeds the {tolerance:.0%} tolerance set for this run."
            if rel > tolerance else
            f"Overrides reach {round(-loss):,} more people than the unconstrained plan. "
            f"Greedy selection is not optimal, so a constraint can occasionally improve it."
            if loss < 0 else
            f"Overrides forgo {round(loss):,} people, {rel:.1%} of the achievable "
            f"coverage, within the {tolerance:.0%} tolerance set for this run."
        ),
    }
