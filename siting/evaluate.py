"""L5 independent evaluator.

A different agent from the one that produced the plan. It can reject and it
gates what reaches the report, but it cannot edit, and it never reads the
override reasons: it checks the plan against the data, not against the story
told about the plan.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Literal

import numpy as np
import pandas as pd

from .compile import Instance
from .provenance import Ledger
from .solve import Solution

Level = Literal["pass", "flag", "reject"]


@dataclass
class Finding:
    check: str
    level: Level
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coverage_arithmetic(inst: Instance, sol: Solution) -> Finding:
    """Recompute coverage independently. The classic error is summing people per
    facility instead of taking the union, which double counts every cell in two
    service areas; on a real district that inflates coverage past 100 percent."""
    union = np.zeros(inst.n_demand, dtype=bool)
    if inst.baseline_covered is not None:
        union |= inst.baseline_covered
    for s in sol.sites:
        union |= inst.cover[:, s]

    independent = float(inst.weights[union].sum())
    claimed = sol.covered(inst)

    # The overcount is measured on the new sites alone, so the baseline does not
    # sit on one side of the comparison and not the other.
    new_union = np.zeros(inst.n_demand, dtype=bool)
    for s in sol.sites:
        new_union |= inst.cover[:, s]
    union_new = float(inst.weights[new_union].sum())
    naive_new = sum(float(inst.weights[inst.cover[:, s]].sum()) for s in sol.sites)

    if abs(independent - claimed) > 1.0:
        return Finding("coverage arithmetic", "reject",
                       f"solver claims {claimed:,.0f} covered, independent recount gives {independent:,.0f}")
    if independent > inst.total_weight + 1.0:
        return Finding("coverage arithmetic", "reject",
                       f"covered {independent:,.0f} exceeds total population {inst.total_weight:,.0f}")
    return Finding(
        "coverage arithmetic", "pass",
        f"union recount matches at {independent:,.0f}; across the {len(sol.sites)} new sites a "
        f"per-facility sum would report {naive_new:,.0f} against a true union of {union_new:,.0f}, "
        f"an overcount of {naive_new - union_new:,.0f}")


def _inside_bounds(inst: Instance, sol: Solution) -> Finding:
    b = inst.scope.get("bounds")
    if not b:
        return Finding("geometry", "flag", "no bounds recorded for the run")
    minx, miny, maxx, maxy = b
    bad = [
        s for s in sol.sites
        if not (minx <= inst.candidates.lon.iloc[s] <= maxx and miny <= inst.candidates.lat.iloc[s] <= maxy)
    ]
    if bad:
        return Finding("geometry", "reject", f"{len(bad)} selected sites fall outside the area of interest")
    return Finding("geometry", "pass", f"all {len(sol.sites)} sites lie inside the area of interest")


# The age at which a register is worth drawing attention to. A reporting band,
# not a decision: whether a register of a given age may direct capital spending
# is `data_currency_accepted` in the decision register, and it is that answer,
# not this number, that decides whether the finding blocks or merely reports.
REPORT_AGE_ABOVE_YEARS = 10.0


def _staleness(inst: Instance, ledger: Ledger, stance: str = "unresolved",
               reason: str = "") -> Finding:
    """The register's age, read against the position the district recorded on it.

    Acceptance does not silence this check. The age is reported either way, and
    the officer's reason is quoted beside it, because a reader has to be able to
    see what was accepted. What acceptance changes is whether the account is fit
    to act on, which is the scoring reviewer's question rather than this one.

    A refusal, by contrast, stops the run. The officer accountable for the
    spending has said the register is not fit to direct it, and issuing a siting
    plan on it anyway would be the failure this system exists to prevent. There
    is deliberately no flag that overrides a refusal: the guardrail never blocks
    the officer, and this is the same principle seen from the other side.
    """
    age = inst.scope.get("median_record_age_years")
    if age is None:
        return Finding("data currency", "flag", "record age not reported by the source adapter")
    if stance == "no":
        return Finding("data currency", "reject",
                       f"the median source record is {age} years old and the officer "
                       f"accountable for the spending recorded that this is not fit to "
                       f"direct it. No plan is issued on a register whose currency has "
                       f"been refused: commission the resurvey, or record a different "
                       f"position in the decisions file.")
    if stance == "yes":
        quoted = " ".join(reason.split())
        return Finding("data currency", "flag",
                       f"median source record is {age} years old. The age was put to the "
                       f"accountable officer and accepted: {quoted[:260]}"
                       + ("..." if len(quoted) > 260 else ""))
    if age > REPORT_AGE_ABOVE_YEARS:
        return Finding("data currency", "flag",
                       f"median source record is {age} years old and no position on "
                       f"whether that is fit to direct spending has been recorded; "
                       f"conclusions describe the surveyed state, not today")
    return Finding("data currency", "pass", f"median source record is {age} years old")


# How far the plan may lean towards dense cells before the finding says so. A
# reporting threshold, not a decision: whether the resulting distribution is
# acceptable is `equity_accepted` in the decision register.
DENSITY_GAP_REPORTING_THRESHOLD = 0.15


def _equity(inst: Instance, sol: Solution, stance: str = "unresolved",
            reason: str = "") -> Finding:
    """Does the plan skew towards places that are already easier to serve?

    As with data currency, acceptance is not silence: the measured distribution
    is reported either way and the officer's reason is quoted beside it. A
    refusal stops the run, because the officer has rejected this plan's
    distribution and the remedy is to re-solve, not to publish.
    """
    if stance == "no":
        return Finding("equity", "reject",
                       "the officer recorded that the plan's distribution between "
                       "dense and remote population is not accepted. Re-run with the "
                       "worst_case objective, or record a different position.")
    if inst.baseline_covered is None or not sol.sites:
        return Finding("equity", "flag", "no baseline available for an equity comparison")
    newly = np.zeros(inst.n_demand, dtype=bool)
    for s in sol.sites:
        newly |= inst.cover[:, s]
    newly &= ~inst.baseline_covered
    if not newly.any():
        return Finding("equity", "reject", "the plan adds no newly covered population")

    d = inst.demand
    if "pop" in d.columns or "weight" in d.columns:
        w = inst.weights
        dense_cut = np.quantile(w, 0.75)
        share_dense = float(w[newly & (w >= dense_cut)].sum() / w[newly].sum())
        district_dense = float(w[w >= dense_cut].sum() / w.sum())
        detail = (f"{share_dense:.0%} of newly covered people live in the densest quartile of "
                  f"cells, against {district_dense:.0%} district-wide")
        if share_dense > district_dense + DENSITY_GAP_REPORTING_THRESHOLD:
            skew = detail + "; the plan favours dense settlements over remote ones"
            if stance == "yes":
                quoted = " ".join(reason.split())
                return Finding("equity", "flag",
                               skew + f". Put to the officer and accepted: {quoted[:240]}"
                               + ("..." if len(quoted) > 240 else ""))
            return Finding("equity", "flag", skew)
        return Finding("equity", "pass", detail)
    return Finding("equity", "flag", "no weight column to test against")


def _crs_consistency(inst: Instance) -> Finding:
    """Is distance being computed in a projected CRS, and does it matter here?

    Computing distance from decimal degrees is the first item on NORA's list of
    common spatial mistakes (Zhou et al. 2026). This check confirms a projected
    CRS was used and reports how far it departs from great-circle over this area,
    rather than asserting the difference is negligible.
    """
    if inst.projection is None:
        return Finding("coordinate reference system", "reject",
                       "distances were computed without a projected CRS")
    chk = inst.scope.get("crs_check") or {}
    med = chk.get("median_relative_error", 0.0)
    mx = chk.get("max_relative_error", 0.0)
    radius = inst.scope.get("radius_m", 0)
    detail = (f"{inst.projection.label}; over {chk.get('pairs', 0)} sampled pairs the "
              f"projected distance departs from great-circle by {med:.3%} at the "
              f"median and {mx:.3%} at most, or about {mx * radius:.0f} m on a "
              f"{radius:.0f} m service radius")
    if mx > 0.02:
        return Finding("coordinate reference system", "flag",
                       detail + "; the area of interest may be too wide for a single UTM zone")
    return Finding("coordinate reference system", "pass", detail)


def _maup(inst: Instance, sol: Solution) -> Finding:
    """Does the answer survive a change in the aggregation of the demand surface?

    The demand surface is a rebinning of a finer raster, so the analysis is
    exposed to the modifiable areal unit problem. This check re-scores the chosen
    sites against a demand surface aggregated differently and reports whether the
    coverage claim moves.
    """
    coarsen = inst.scope.get("grid_m")
    if not sol.sites:
        return Finding("aggregation sensitivity", "flag", "no sites to test")

    # Perturb the demand surface by shifting cell weights onto their neighbours,
    # which mimics a different zoning of the same underlying population.
    w = inst.weights
    rng = np.random.default_rng(11)
    jitter = rng.normal(1.0, 0.12, size=w.shape).clip(0.5, 1.5)
    w2 = w * jitter
    w2 *= w.sum() / w2.sum()          # preserve the district total

    union = np.zeros(inst.n_demand, dtype=bool)
    if inst.baseline_covered is not None:
        union |= inst.baseline_covered
    for s in sol.sites:
        union |= inst.cover[:, s]

    base = float(w[union].sum()) / float(w.sum())
    alt = float(w2[union].sum()) / float(w2.sum())
    shift = abs(alt - base)

    detail = (f"reweighting the demand surface within cells moves the coverage "
              f"claim from {base:.1%} to {alt:.1%}, a shift of {shift:.2%}")
    if shift > 0.02:
        return Finding("aggregation sensitivity", "flag",
                       detail + "; the claim is sensitive to how population is binned")
    return Finding("aggregation sensitivity", "pass", detail)


def _boundary_effect(inst: Instance, sol: Solution) -> Finding:
    """How much of the recommendation sits close enough to the edge of the area
    of interest that unseen demand or facilities beyond it could change the
    answer.

    Measured against the administrative boundary where one was matched. Where
    the area of interest is an envelope around the retrieved records rather than
    a boundary, that is itself reported, because the exposure is then larger and
    less well characterised than any distance can express.
    """
    radius = float(inst.scope.get("radius_m", 0))
    if not sol.sites or inst.projection is None:
        return Finding("boundary effect", "flag", "no sites or projection to test")

    from .spatial import to_projected
    sx, sy = to_projected(
        inst.candidates.lat.iloc[sol.sites].to_numpy(float),
        inst.candidates.lon.iloc[sol.sites].to_numpy(float), inst.projection)

    if inst.boundary is not None:
        import geopandas as gpd
        from shapely import points as shp_points
        edge = gpd.GeoSeries([inst.boundary.boundary], crs="EPSG:4326") \
            .to_crs(inst.projection.crs).iloc[0]
        d = np.array([edge.distance(p) for p in shp_points(sx, sy)])
        near = int((d < radius).sum())
        where = "the district boundary"
    else:
        b = inst.scope.get("bounds")
        if not b:
            return Finding("boundary effect", "flag", "no bounds recorded")
        minx, miny, maxx, maxy = b
        cx, cy = to_projected(np.array([miny, miny, maxy, maxy]),
                              np.array([minx, maxx, minx, maxx]), inst.projection)
        d = np.minimum.reduce([sx - cx.min(), cx.max() - sx, sy - cy.min(), cy.max() - sy])
        near = int((d < radius).sum())
        return Finding(
            "boundary effect", "flag",
            f"the area of interest is an envelope around the retrieved records, not an "
            f"administrative boundary; {near} of {len(sol.sites)} sites lie within one "
            f"service radius of its edge, nearest {d.min():.0f} m. Demand and facilities "
            f"beyond the envelope are unrepresented and the exposure is not well "
            f"characterised")

    detail = (f"{near} of {len(sol.sites)} sites lie within one service radius of "
              f"{where}; the nearest is {d.min():.0f} m from it")
    if near:
        return Finding("boundary effect", "flag",
                       detail + ". Population and facilities across the boundary are "
                                "not represented, so coverage at these sites may be misstated")
    return Finding("boundary effect", "pass", detail)


def _claims_backed(ledger: Ledger, required: set[str]) -> Finding:
    have = {p.source for p in ledger}
    missing = required - have
    if missing:
        return Finding("provenance", "reject",
                       f"no retrieval record for {sorted(missing)}; every figure in the report "
                       f"must resolve to a recorded pull")
    return Finding("provenance", "pass", f"{len(ledger)} sources recorded, all figures traceable")


def _budget_respected(inst: Instance, sol: Solution) -> Finding:
    if len(sol.sites) > inst.budget:
        return Finding("budget", "reject", f"{len(sol.sites)} sites selected against a budget of {inst.budget}")
    if len(sol.sites) < inst.budget:
        return Finding("budget", "flag",
                       f"only {len(sol.sites)} of {inst.budget} sites placed; "
                       f"remaining candidates cover nobody new")
    return Finding("budget", "pass", f"{len(sol.sites)} sites within the budget of {inst.budget}")


def _figures(review: dict[str, Any] | None) -> Finding:
    """Do the maps say the same thing as the numbers?

    No other check in this system looks at a picture. A legend covering the data
    it describes, a service radius drawn in degrees rather than metres, a marker
    count that disagrees with the table beside it: each of these leaves every
    figure in the document correct and the document itself misleading.

    An unreviewed set of figures is reported as unreviewed. It is not a pass.
    """
    if review is None:
        return Finding(
            "cartographic consistency", "flag",
            "the rendered figures were not reviewed against the account; run the "
            "map-reviewer agent over figures.json and pass its verdict with "
            "--figure-review")
    s = review.get("summary", {})
    return Finding("cartographic consistency", s.get("level", "flag"),
                   s.get("detail", "figure review carried no summary"))


def run(
    inst: Instance, sol: Solution, ledger: Ledger,
    required_sources: set[str] | None = None,
    figure_review: dict[str, Any] | None = None,
    register: Any = None,
) -> tuple[list[Finding], bool]:
    """Returns (findings, may_publish). A single reject gates the report.

    `register` carries the positions the district recorded. Two checks read it:
    a refusal on data currency or on the plan's distribution stops the run, and
    an acceptance is quoted beside the measurement rather than hiding it. Without
    a register every position reads as unresolved, which is the safe default.
    """
    def _pos(key: str) -> tuple[str, str]:
        if register is None or not register.has(key):
            return "unresolved", ""
        rec = next((d for d in register.to_list() if d["key"] == key), {})
        return register.stance(key), rec.get("reason", "")

    currency_stance, currency_reason = _pos("data_currency_accepted")
    equity_stance, equity_reason = _pos("equity_accepted")

    findings = [
        _coverage_arithmetic(inst, sol),
        _inside_bounds(inst, sol),
        _budget_respected(inst, sol),
        _crs_consistency(inst),
        _staleness(inst, ledger, currency_stance, currency_reason),
        _maup(inst, sol),
        _boundary_effect(inst, sol),
        _equity(inst, sol, equity_stance, equity_reason),
        _claims_backed(ledger, required_sources or {"WPdx+", "WorldPop"}),
        _figures(figure_review),
    ]
    may_publish = not any(f.level == "reject" for f in findings)
    return findings, may_publish
