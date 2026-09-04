"""L3 solver.

Greedy submodular maximisation is the primary engine: it carries the standard
(1 - 1/e) guarantee and re-solves in milliseconds, which is what the override
loop needs. spopt's exact MCLP is available as a benchmark on small instances,
but CBC takes over two minutes on a 300 x 60 toy problem and is not usable
interactively; `benchmark()` exists to quantify the greedy gap, not to run in
the loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .compile import Instance


@dataclass
class Solution:
    sites: list[int]                    # candidate indices, in selection order
    marginal: list[float]               # people newly covered by each pick
    covered_mask: np.ndarray            # bool [n_demand], union of all coverage
    guarantee: str = "greedy, (1 - 1/e) of optimal coverage"
    notes: list[str] = field(default_factory=list)

    def covered(self, inst: Instance) -> float:
        return float(inst.weights[self.covered_mask].sum())

    def share(self, inst: Instance) -> float:
        return self.covered(inst) / inst.total_weight if inst.total_weight else 0.0

    def curve(self, inst: Instance) -> list[dict[str, Any]]:
        """Cumulative coverage after each facility. This is the figure that answers
        'how many should we build', which is the question officials actually ask."""
        base = float(inst.weights[inst.baseline_covered].sum()) if inst.baseline_covered is not None else 0.0
        total = inst.total_weight or 1.0
        out, running = [], base
        for n, gain in enumerate(self.marginal, start=1):
            running += gain
            out.append({
                "n": n,
                "marginal": round(gain),
                "cumulative": round(running),
                "cumulative_share": round(running / total, 4),
            })
        return out


def site_ids(sol: Solution) -> dict[int, str]:
    """The human-facing label for each candidate this solution selected.

    Lives beside `Solution` rather than in `results.py` (which re-exports it)
    because `overrides.price()` needs it to resolve an officer's override
    target against whichever solution they were actually looking at when they
    wrote it down — and `results.py` imports `overrides.py`, so the reverse
    import would be circular.
    """
    return {c: f"S-{n:03d}" for n, c in enumerate(sol.sites, start=1)}


def _uncovered_gain(
    cover: np.ndarray, weights: np.ndarray, open_mask: np.ndarray, allowed: np.ndarray
) -> np.ndarray:
    """People each still-allowed candidate would newly cover."""
    gains = (cover[~open_mask].T * weights[~open_mask]).sum(axis=1)
    gains[~allowed] = -1.0
    return gains


def greedy(inst: Instance) -> Solution:
    """Pick the facility that covers the most still-uncovered people, repeat.

    Coverage is the union over selected facilities, never the sum per facility;
    summing double counts every cell reachable from two sites.
    """
    w = inst.weights
    covered = (
        inst.baseline_covered.copy()
        if inst.baseline_covered is not None
        else np.zeros(inst.n_demand, dtype=bool)
    )

    allowed = np.ones(inst.n_candidates, dtype=bool)
    allowed[inst.must_exclude] = False

    sites: list[int] = []
    marginal: list[float] = []
    notes: list[str] = []

    # Separation is enforced in the projected CRS for the same reason coverage is.
    if inst.projection is not None and inst.min_separation_m > 0:
        from .spatial import to_projected
        cand_x, cand_y = to_projected(
            inst.candidates["lat"].to_numpy(float),
            inst.candidates["lon"].to_numpy(float),
            inst.projection,
        )
    else:
        cand_x = cand_y = None

    def block_neighbours(idx: int) -> None:
        if inst.min_separation_m <= 0 or cand_x is None:
            return
        d2 = (cand_x - cand_x[idx]) ** 2 + (cand_y - cand_y[idx]) ** 2
        allowed[d2 < inst.min_separation_m ** 2] = False

    # Pinned sites are placed first and their cost is charged against the budget.
    for idx in inst.must_include:
        if not allowed[idx]:
            notes.append(f"candidate {idx} is both pinned and vetoed; the veto wins")
            continue
        gain = float(w[inst.cover[:, idx] & ~covered].sum())
        covered |= inst.cover[:, idx]
        sites.append(int(idx))
        marginal.append(gain)
        allowed[idx] = False
        block_neighbours(int(idx))

    while len(sites) < inst.budget:
        gains = _uncovered_gain(inst.cover, w, covered, allowed)
        if gains.size == 0 or gains.max() <= 0:
            notes.append("stopped early: no remaining candidate covers anyone new")
            break
        pick = int(np.argmax(gains))
        sites.append(pick)
        marginal.append(float(gains[pick]))
        covered |= inst.cover[:, pick]
        allowed[pick] = False
        block_neighbours(pick)

    return Solution(sites=sites, marginal=marginal, covered_mask=covered, notes=notes,
                    guarantee=OBJECTIVE_META["max_coverage"]["guarantee"])


def unconstrained_optimum(inst: Instance) -> Solution:
    """No pins and no vetoes, under the instance's own objective. The reference
    every override is priced against."""
    return solve(inst.variant(must_include=[], must_exclude=[]))


def benchmark(inst: Instance, time_limit_s: int = 300) -> dict[str, Any]:
    """Exact MCLP via spopt, for the paper's greedy-gap number. Not for the loop."""
    # MCLP is the exact form of maximum coverage. Against a p-centre plan it
    # measures a different quantity, and reporting the two side by side would read
    # as a gap where there is only a difference of objective.
    if inst.objective != "max_coverage":
        return {"status": "skipped",
                "reason": f"the exact MCLP benchmark measures maximum coverage, and "
                          f"this run's objective is {inst.objective}; the two are not "
                          f"comparable"}
    # The solver is optional. A missing package must not take down a run whose
    # analysis has already completed.
    try:
        import pulp
        from spopt.locate import MCLP
    except ImportError as exc:
        return {"status": "skipped",
                "reason": f"the exact solver is not installed ({exc}); "
                          f"pip install spopt pulp to run the benchmark"}

    n_d, n_c = inst.n_demand, inst.n_candidates
    if n_d * n_c > 400_000:
        return {"status": "skipped", "reason": f"instance too large for CBC ({n_d} x {n_c})"}

    # spopt wants a cost matrix; reuse the coverage matrix as a 0/1 cost with a
    # radius of 0.5 so that "cost 0" means covered.
    cost = np.where(inst.cover, 0.0, 1.0)
    try:
        m = MCLP.from_cost_matrix(cost, inst.weights, service_radius=0.5, p_facilities=inst.budget)
        m = m.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit_s))
    except Exception as exc:  # CBC failure should never take the run down
        return {"status": "failed", "reason": str(exc)[:200]}

    chosen = {c for f in m.fac2cli for c in f}          # union, not sum
    covered = float(inst.weights[sorted(chosen)].sum()) if chosen else 0.0
    return {
        "status": "solved",
        "covered": round(covered),
        "share": round(covered / inst.total_weight, 4) if inst.total_weight else 0.0,
    }


def p_centre(inst: Instance) -> Solution:
    """Reduce the distance faced by the worst-served population.

    The other half of the objective decision. Maximum coverage reaches the
    largest number and concentrates on settled areas by construction; this
    reaches the households that are currently furthest from service, at a lower
    total covered. Greedy furthest-first insertion, a 2-approximation for the
    metric p-centre problem.

    Distance is weighted before the worst cell is chosen, so that an unserved
    hamlet of three people does not outrank a village of three hundred at the
    same distance. That weighting is a judgement, carries as `inst.equity_exponent`
    (0.5 by default), and is stated in the notes; a REWEIGHT override changes it
    and nothing else.

    On the walking_time basis, "distance" here means minutes by the same
    local-friction approximation `coverage_by_travel_time` uses for coverage
    itself: friction sampled at a facility or candidate, applied uniformly in
    every direction from it. This is the same approximation the district
    already accepted for coverage, not a second, more precise method — see
    `siting.sources.friction`.

    Returned in the same shape as `greedy`, so nothing downstream needs to know
    which objective ran; `marginal` still means population newly covered, not
    the quantity being optimised.
    """
    from .spatial import to_projected

    if inst.projection is None or inst.baseline_distance_m is None:
        raise ValueError(
            "the worst_case objective needs a projected CRS and a baseline distance "
            "surface, and this instance carries neither. Falling back to maximum "
            "coverage would answer a different question from the one the district "
            "recorded, so the run stops here instead of substituting an objective.")

    basis = inst.scope.get("coverage_basis", "straight_line")
    cand_friction = None
    if basis == "walking_time":
        if inst.friction_arr is None:
            raise ValueError(
                "the worst_case objective on a walking_time run needs the friction "
                "surface the coverage figures were built from, and this instance "
                "carries none.")
        from .sources.friction import sample
        cand_friction = sample(inst.friction_arr, inst.friction_transform,
                               inst.candidates["lat"].to_numpy(float),
                               inst.candidates["lon"].to_numpy(float))
        finite = np.isfinite(cand_friction) & (cand_friction > 0)
        if not finite.any():
            raise RuntimeError(
                "every candidate falls outside the friction surface's finite "
                "cells; the worst_case objective cannot measure walking time here.")
        cand_friction = np.where(finite, cand_friction, np.median(cand_friction[finite]))

    w = inst.weights
    covered = (
        inst.baseline_covered.copy()
        if inst.baseline_covered is not None
        else np.zeros(inst.n_demand, dtype=bool)
    )
    allowed = np.ones(inst.n_candidates, dtype=bool)
    allowed[inst.must_exclude] = False

    dx, dy = to_projected(inst.demand["lat"].to_numpy(float),
                          inst.demand["lon"].to_numpy(float), inst.projection)
    cx, cy = to_projected(inst.candidates["lat"].to_numpy(float),
                          inst.candidates["lon"].to_numpy(float), inst.projection)

    nearest = inst.baseline_distance_m.copy()
    finite = np.isfinite(nearest)
    if finite.any():
        nearest[~finite] = nearest[finite].max() * 2
    else:
        nearest[:] = 0.0

    exponent = inst.equity_exponent
    sites: list[int] = []
    marginal: list[float] = []
    notes = [
        "objective: reduce the distance faced by the worst-served population",
        f"the worst cell is chosen on distance weighted by population^{exponent:g}: "
        + (
            "population plays no part, so the single most distant cell wins "
            "regardless of its size"
            if exponent == 0
            else "distance and population are weighted equally, so a large "
            "underserved village can outrank a barely-more-distant hamlet"
            if exponent == 1
            else "a very small settlement does not outrank a much larger one at "
            "the same distance, but does not vanish against it either"
        ),
    ]
    if basis == "walking_time":
        notes.append(
            "distance is minutes of walking over the friction surface, by the "
            "same local-friction approximation the coverage figures use: "
            "friction sampled at a facility or candidate and applied uniformly "
            "in every direction from it, not a least-cost path"
        )

    def place(idx: int) -> None:
        nonlocal nearest, covered
        gain = float(w[inst.cover[:, idx] & ~covered].sum())
        step = np.hypot(dx - cx[idx], dy - cy[idx])
        if basis == "walking_time":
            step = step * cand_friction[idx]     # metres x minutes/metre = minutes
        nearest = np.minimum(nearest, step)
        covered |= inst.cover[:, idx]
        sites.append(int(idx))
        marginal.append(gain)
        allowed[idx] = False
        if inst.min_separation_m > 0:
            near = (cx - cx[idx]) ** 2 + (cy - cy[idx]) ** 2 < inst.min_separation_m ** 2
            allowed[near] = False

    for idx in inst.must_include:
        if allowed[idx]:
            place(int(idx))

    radius = float(
        (inst.scope.get("coverage_minutes") if basis == "walking_time"
         else inst.scope.get("radius_m")) or 0
    )
    while len(sites) < inst.budget:
        score = nearest * np.maximum(w, 1.0) ** exponent
        score[nearest <= radius] = -1.0        # already served; not the worst case
        if not (score > 0).any():
            notes.append("stopped early: no population remains beyond the service radius")
            break

        target = int(np.argmax(score))
        d_to_target = np.hypot(cx - dx[target], cy - dy[target])
        if basis == "walking_time":
            d_to_target = d_to_target * cand_friction   # which candidate reaches it fastest
        d_to_target[~allowed] = np.inf
        if not np.isfinite(d_to_target).any():
            notes.append("stopped early: no candidate remains after separation constraints")
            break
        place(int(np.argmin(d_to_target)))

    sol = Solution(sites=sites, marginal=marginal, covered_mask=covered, notes=notes)
    sol.guarantee = worst_case_description(exponent, basis)["guarantee"]
    return sol


def worst_case_description(exponent: float, basis: str = "straight_line") -> dict[str, str]:
    """The worst_case objective's method and guarantee text, as a function of
    the population-weighting exponent a REWEIGHT override may have changed, and
    of the coverage basis the district chose.

    Kept separate from `OBJECTIVE_META` because that dict is static and this
    text is not: a run whose exponent departs from the 0.5 default must not
    have the report still say 'weighted by the square root of its size', and a
    walking_time run must not have it claim the precision of a distance the
    method does not actually compute.
    """
    if exponent == 0:
        weighting = ("not weighted by population at all, so the single most "
                     "distant cell wins regardless of how many people live there")
        bound = ("No population weighting is applied, so this run solves the "
                 "unweighted metric p-centre problem the bound describes.")
    elif exponent == 1:
        weighting = ("weighted equally by population, so a large underserved "
                     "village can outrank a barely-more-distant hamlet")
        bound = ("The population weighting is a district judgement that departs "
                 "from that objective, so no bound is claimed for the weighted "
                 "problem actually solved here.")
    else:
        weighting = (f"weighted by population^{exponent:g}, so a very small "
                     f"settlement does not outrank a much larger one at the same "
                     f"distance, but does not vanish against it either")
        bound = ("The population weighting is a district judgement that departs "
                 "from that objective, so no bound is claimed for the weighted "
                 "problem actually solved here.")
    if basis == "walking_time":
        measure = ("minutes of walking over the friction surface, estimated by the "
                   "same local-friction approximation the coverage figures use "
                   "(friction sampled at a facility or candidate and applied "
                   "uniformly in every direction from it, not a least-cost path)")
        bound += (" That approximation, not a least-cost path, is also what this "
                  "objective's distances are built from, so the 2-approximation "
                  "bound is for the metric problem it would solve on exact "
                  "distances, not the one actually measured here.")
    else:
        measure = "straight-line distance in the projected CRS"
    return {
        "label": "reducing the distance faced by the worst-served population",
        "short": "worst-served first",
        "selection": f"The worst-served population is located by {measure}, "
                     f"{weighting}, and a facility is placed as close to it as "
                     f"the candidate set allows.",
        "guarantee": "Greedy furthest-first insertion is a 2-approximation for the "
                     f"unweighted metric p-centre problem. {bound}",
    }


OBJECTIVES = {"max_coverage": greedy, "worst_case": p_centre}


def solve(inst: Instance) -> Solution:
    """Solve under the objective the instance carries.

    Every re-solve goes through here. An override loop or a sensitivity scenario
    that reached for `greedy` directly would silently discard the objective the
    district recorded, which is the one judgement in this system that is least
    the machine's to make.
    """
    fn = OBJECTIVES.get(inst.objective)
    if fn is None:
        raise ValueError(
            f"objective {inst.objective!r} is not one of {sorted(OBJECTIVES)}")
    return fn(inst)


# How each objective describes itself, in the words the report uses.
#
# Six places across the two templates, the exhibits and the scoring reviewer used
# to carry this prose hard-coded, which is why a p-centre run's cover page said
# "greedy coverage maximisation". They all read from here now, so there is one
# place to change it and no way for two of them to disagree.
OBJECTIVE_META: dict[str, dict[str, str]] = {
    "max_coverage": {
        "label": "maximum coverage",
        "short": "most people reached",
        "selection": "Candidates are scored by the population they would newly "
                     "bring inside the service radius and selected greedily.",
        "guarantee": "On a monotone submodular coverage function greedy selection "
                     "is within a factor of 1 - 1/e, about 63 per cent, of the "
                     "optimum.",
    },
    "worst_case": {
        "label": "reducing the distance faced by the worst-served population",
        "short": "worst-served first",
        # No "selection"/"guarantee" here: those depend on the population
        # weighting exponent, which a REWEIGHT override can change per run.
        # `worst_case_description(inst.equity_exponent)` produces them instead.
    },
}
