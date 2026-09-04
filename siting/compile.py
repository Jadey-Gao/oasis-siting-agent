"""L2 problem compiler.

Every domain compiles into the same four slots. Nothing downstream of this
module knows which domain it is solving, which is the whole point: a fifth
domain is an adapter and a config entry, not a second codebase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .clean import haversine_m
from .spatial import Projection, to_projected, utm_for, within_radius


@dataclass
class Instance:
    """The canonical siting problem. Four slots, filled identically by every domain."""

    # demand
    demand: pd.DataFrame          # lat, lon, weight, plus domain columns
    # candidates
    candidates: pd.DataFrame      # lat, lon, kind, plus domain columns
    # coverage
    cover: np.ndarray             # bool [n_demand, n_candidate]
    # budget and hard sets
    budget: int
    must_include: list[int] = field(default_factory=list)
    must_exclude: list[int] = field(default_factory=list)
    # Two recommendations 300 m apart waste a budget line. Real programmes space
    # facilities out, so the solver is told to as well.
    min_separation_m: float = 0.0

    # context carried through to the report
    projection: Projection | None = None
    domain: str = ""
    coverage_rule: str = ""
    # The objective the district recorded. It belongs to the problem, not to the
    # solving of it, so it travels with the instance: anything that re-solves this
    # instance re-solves it under the objective the officer actually chose. An
    # earlier version read the objective once in the CLI and then discarded it, so
    # every override and every sensitivity scenario silently reverted to maximum
    # coverage and the report described a method the run had not used.
    objective: str = "max_coverage"
    scope: dict[str, Any] = field(default_factory=dict)
    baseline_covered: np.ndarray | None = None   # bool [n_demand], served today
    # Distance from each demand cell to the nearest facility serving it today, or
    # infinity where none exists. The worst_case objective optimises this
    # directly, so it cannot be reconstructed from the coverage matrix, which
    # only records whether a cell is inside some radius. Metres on the
    # straight_line basis; minutes, by the same local-friction approximation
    # `coverage_by_travel_time` uses, on the walking_time basis — the unit
    # travels with `scope["coverage_basis"]`, which `p_centre` reads before
    # comparing this against a threshold.
    baseline_distance_m: np.ndarray | None = None
    # How much the worst_case objective lets population size count when it
    # chooses who is worst-served: 0 is the worst-off cell wins regardless of
    # how many people live there, 1 weights that choice by population. 0.5 is
    # the district default (the square root used in `p_centre`); a REWEIGHT
    # override changes this and nothing else, and only when the objective is
    # worst_case, since max_coverage has no such balance to shift.
    equity_exponent: float = 0.5
    # The walking friction surface and its affine transform, carried so the
    # worst_case objective can convert distance to placed candidates into
    # minutes as it goes, on the walking_time basis. None on straight_line.
    friction_arr: Any = None
    friction_transform: Any = None
    # The administrative boundary, when one could be matched. Where this is None
    # the area of interest is an envelope around the retrieved records and the
    # assessment says so, because an envelope is not a district.
    boundary: Any = None

    @property
    def weights(self) -> np.ndarray:
        return self.demand["weight"].to_numpy(dtype=float)

    @property
    def total_weight(self) -> float:
        return float(self.weights.sum())

    @property
    def n_demand(self) -> int:
        return len(self.demand)

    @property
    def n_candidates(self) -> int:
        return len(self.candidates)

    def baseline_share(self) -> float:
        if self.baseline_covered is None or self.total_weight == 0:
            return 0.0
        return float(self.weights[self.baseline_covered].sum() / self.total_weight)

    def summary(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "objective": self.objective,
            "coverage_rule": self.coverage_rule,
            "demand_cells": self.n_demand,
            "candidates": self.n_candidates,
            "budget": self.budget,
            "population": round(self.total_weight),
            "baseline_covered_share": round(self.baseline_share(), 4),
            "must_include": len(self.must_include),
            "must_exclude": len(self.must_exclude),
            "min_separation_m": self.min_separation_m,
            "crs": self.projection.label if self.projection else "unprojected",
        }

    def variant(self, **changes: Any) -> "Instance":
        """A copy with some fields changed, for a scenario or an override run.

        Every field not named in `changes` is carried across automatically, so
        adding a field to this class can never silently drop it from a copy. Three
        hand-written copy constructors used to enumerate the fields by hand and all
        three had already dropped `must_include` and `must_exclude`, which is how a
        sensitivity scenario came to re-recommend a site the officer had vetoed.

        The two hard sets are rebuilt rather than shared: `overrides.apply` extends
        them in place, and a shared list would let one scenario's veto leak into
        another's.
        """
        from dataclasses import replace
        base = {"must_include": list(self.must_include),
                "must_exclude": list(self.must_exclude)}
        return replace(self, **{**base, **changes})


def coverage_matrix(
    demand: pd.DataFrame, candidates: pd.DataFrame, radius_m: float, proj: Projection
) -> np.ndarray:
    """Straight-line coverage in a projected CRS, chunked to bound memory.

    Distances are Euclidean in `proj` rather than great-circle on the sphere:
    computing distance from decimal degrees is the first item on NORA's list of
    common spatial mistakes, and a service radius is exactly the kind of claim
    that has to hold in metres on the ground.
    """
    dx, dy = to_projected(demand["lat"].to_numpy(float), demand["lon"].to_numpy(float), proj)
    cx, cy = to_projected(candidates["lat"].to_numpy(float), candidates["lon"].to_numpy(float), proj)

    out = np.zeros((len(demand), len(candidates)), dtype=bool)
    step = max(1, int(4e7 // max(1, len(candidates))))
    for i in range(0, len(demand), step):
        j = min(i + step, len(demand))
        out[i:j] = within_radius(dx[i:j, None] - cx[None, :], dy[i:j, None] - cy[None, :], radius_m)
    return out


def served_today(
    demand: pd.DataFrame, existing: pd.DataFrame, radius_m: float, proj: Projection
) -> np.ndarray:
    """Which demand cells already sit inside the service radius of a working facility."""
    if existing.empty:
        return np.zeros(len(demand), dtype=bool)
    return coverage_matrix(demand, existing, radius_m, proj).any(axis=1)


def coverage_by_travel_time(
    demand: pd.DataFrame,
    candidates: pd.DataFrame,
    minutes: float,
    friction_arr,
    transform,
) -> np.ndarray:
    """Coverage where reach is walking time over a friction surface.

    One least-cost accumulation per candidate would be correct and is far too
    slow at several thousand candidates. Instead the traverse cost is converted
    into an effective local speed and coverage is tested as a distance in metres
    that varies by candidate, which is exact where terrain is locally uniform and
    conservative where it is not: a candidate sitting beside an obstacle gets the
    obstacle's speed applied in every direction.

    The approximation is stated in the assessment rather than left implicit. It
    is the reason the walking-time basis is offered as a decision and not as a
    strictly better replacement for straight-line distance.
    """
    from .sources.friction import sample

    fr = sample(friction_arr, transform, candidates["lat"].to_numpy(float),
                candidates["lon"].to_numpy(float))
    fr = np.where(np.isfinite(fr) & (fr > 0), fr, np.nanmedian(fr[np.isfinite(fr)]))
    reach_m = minutes / fr                     # minutes / (minutes per metre)

    dx, dy = to_projected(demand["lat"].to_numpy(float), demand["lon"].to_numpy(float), _PROJ[0])
    cx, cy = to_projected(candidates["lat"].to_numpy(float), candidates["lon"].to_numpy(float), _PROJ[0])

    out = np.zeros((len(demand), len(candidates)), dtype=bool)
    step = max(1, int(4e7 // max(1, len(candidates))))
    for i in range(0, len(demand), step):
        j = min(i + step, len(demand))
        d2 = (dx[i:j, None] - cx[None, :]) ** 2 + (dy[i:j, None] - cy[None, :]) ** 2
        out[i:j] = d2 <= (reach_m ** 2)[None, :]
    return out


# The projection in force for the current instance, set by the domain adapter so
# the travel-time path does not have to thread it through every call.
_PROJ: list = [None]


def set_projection(proj) -> None:
    _PROJ[0] = proj


def distance_to_nearest(
    demand: pd.DataFrame, facilities: pd.DataFrame, proj: Projection
) -> np.ndarray:
    """Metres from each demand cell to its nearest facility, infinity if none."""
    if facilities.empty:
        return np.full(len(demand), np.inf)
    dx, dy = to_projected(demand["lat"].to_numpy(float), demand["lon"].to_numpy(float), proj)
    fx, fy = to_projected(facilities["lat"].to_numpy(float), facilities["lon"].to_numpy(float), proj)

    out = np.full(len(demand), np.inf)
    step = max(1, int(4e7 // max(1, len(facilities))))
    for i in range(0, len(demand), step):
        j = min(i + step, len(demand))
        d2 = (dx[i:j, None] - fx[None, :]) ** 2 + (dy[i:j, None] - fy[None, :]) ** 2
        out[i:j] = np.sqrt(d2.min(axis=1))
    return out


def travel_minutes_to_nearest(
    demand: pd.DataFrame, facilities: pd.DataFrame, friction_arr, transform,
) -> np.ndarray:
    """Minutes from each demand cell to its nearest facility, infinity if none.

    The walking_time counterpart to `distance_to_nearest`, by the same
    local-friction approximation `coverage_by_travel_time` uses: friction is
    sampled at the facility and applied uniformly in every direction from it,
    so a river between the facility and a demand cell does not lengthen the
    estimate. The approximation is the one the district accepted for coverage
    itself; this is not a second, more precise method.
    """
    from .sources.friction import sample

    if facilities.empty:
        return np.full(len(demand), np.inf)

    fr = sample(friction_arr, transform, facilities["lat"].to_numpy(float),
                facilities["lon"].to_numpy(float))
    finite = np.isfinite(fr) & (fr > 0)
    if not finite.any():
        raise RuntimeError(
            "every facility falls outside the friction surface's finite cells; "
            "the worst_case objective cannot measure walking time here.")
    fr = np.where(finite, fr, np.median(fr[finite]))

    dx, dy = to_projected(demand["lat"].to_numpy(float), demand["lon"].to_numpy(float), _PROJ[0])
    fx, fy = to_projected(facilities["lat"].to_numpy(float), facilities["lon"].to_numpy(float), _PROJ[0])

    out = np.full(len(demand), np.inf)
    step = max(1, int(4e7 // max(1, len(facilities))))
    for i in range(0, len(demand), step):
        j = min(i + step, len(demand))
        d = np.sqrt((dx[i:j, None] - fx[None, :]) ** 2 + (dy[i:j, None] - fy[None, :]) ** 2)
        out[i:j] = (d * fr[None, :]).min(axis=1)     # metres x minutes/metre = minutes
    return out


def candidate_grid(
    bounds: tuple[float, float, float, float], spacing_m: float, proj: Projection
) -> pd.DataFrame:
    """A regular lattice of greenfield candidate sites, laid out in the projected
    CRS so that spacing is uniform on the ground rather than in degrees."""
    from pyproj import Transformer

    minx, miny, maxx, maxy = bounds
    corners_lon = np.array([minx, maxx, minx, maxx])
    corners_lat = np.array([miny, miny, maxy, maxy])
    cx, cy = to_projected(corners_lat, corners_lon, proj)

    xs = np.arange(cx.min(), cx.max() + spacing_m, spacing_m)
    ys = np.arange(cy.min(), cy.max() + spacing_m, spacing_m)
    gx, gy = np.meshgrid(xs, ys)

    back = Transformer.from_crs(proj.crs, "EPSG:4326", always_xy=True)
    lon, lat = back.transform(gx.ravel(), gy.ravel())

    inside = (lon >= minx) & (lon <= maxx) & (lat >= miny) & (lat <= maxy)
    return pd.DataFrame({"lat": lat[inside], "lon": lon[inside], "kind": "greenfield"})
