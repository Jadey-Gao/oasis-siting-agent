"""Water domain adapter.

Demand is population that cannot reach a working water point on foot.
Candidates are the broken points already on the ground plus a greenfield
lattice, because rehabilitating an existing borehole is usually cheaper than
drilling a new one and the report should be able to say which is which.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..compile import (Instance, candidate_grid, coverage_by_travel_time,
                       coverage_matrix, distance_to_nearest, served_today,
                       set_projection, travel_minutes_to_nearest)
from ..solve import OBJECTIVE_META
from ..spatial import crs_disagreement, utm_for
from ..provenance import Ledger, Notebook
from ..sources import friction as friction_src
from ..sources import gadm, worldpop, wpdx

# There is no default service radius. What counts as served is a judgement about
# who the assessment says already has water, and it is recorded in the decision
# register rather than fixed here. See siting/decisions.py.
#
# Lattice spacing is a technical parameter and is derived from the radius: three
# quarters of it puts a candidate close enough to every viable location without
# producing a column count the solver cannot carry.
GRID_FRACTION_OF_RADIUS = 0.75

# Two recommendations closer together than one service radius largely duplicate
# each other's catchment and waste a budget line, so a programme spaces them at
# least that far apart. Derived from the radius rather than chosen independently,
# in the same way the lattice spacing is, and reported in the assessment so the
# derivation is visible rather than implied.
SEPARATION_FRACTION_OF_RADIUS = 1.0

META = {
    "key": "water",
    "title": "Rural water point siting",
    "demand_label": "Population beyond walking distance of a working water point",
    "candidate_label": "Broken water points and greenfield sites",
    "unit": "water point",
}


def _inside(frame: pd.DataFrame, polygon) -> np.ndarray:
    """Boolean mask of the rows whose point falls inside the polygon.

    Prepared geometry, because a district against twenty thousand demand cells
    is slow enough one point at a time to matter.
    """
    from shapely import points as shp_points
    from shapely.prepared import prep
    pts = shp_points(frame["lon"].to_numpy(float), frame["lat"].to_numpy(float))
    pg = prep(polygon)
    return np.fromiter((pg.covers(p) for p in pts), dtype=bool, count=len(pts))


def _record_anomalies(points, notebook: Notebook, ledger: Ledger) -> None:
    """What the register actually says, as opposed to what a schema suggests."""
    values = points["status_clean"].astype(str).str.strip().value_counts()
    if len(values) > 2:
        listed = "; ".join(f"{k} ({v:,})" for k, v in values.items())
        notebook.note(
            source="WPdx+", kind="semantics",
            observed=f"status_clean is not two-valued in this district. Values present: {listed}.",
            handling="Serving status is read from the status_semantics block in "
                     "handbooks/wpdx.yaml, which treats Functional and Functional, "
                     "needs repair as serving and everything else as not serving.",
            consequence="An equality test against the string Functional would score "
                        "every point in this district as not serving and inflate the gap.",
        )
    if int(points["status_unrecognised"].sum()):
        n = int(points["status_unrecognised"].sum())
        notebook.note(
            source="WPdx+", kind="semantics",
            observed=f"{n:,} records carry a status string not listed in the handbook.",
            handling="Treated as not serving, which is the conservative reading.",
            consequence="The gap may be overstated by up to these records.",
        )

    age = float(points["days_since_report"].median()) / 365.25
    if age > 3:
        notebook.note(
            source="WPdx+", kind="currency",
            observed=f"The median record in this district is {age:.1f} years old "
                     f"(oldest {points['days_since_report'].max() / 365.25:.1f} years, "
                     f"newest {points['days_since_report'].min() / 365.25:.1f} years).",
            handling="Age is reported, not filtered. Uganda district holdings range "
                     "from a mean of six to over sixteen years, so a staleness filter "
                     "would delete whole districts rather than clean them.",
            consequence="Coverage figures describe the surveyed state of the network, "
                        "not necessarily its state today.",
        )

    pull = ledger.by_source("WPdx+")
    if pull and pull.drops:
        for reason, n in pull.drops.items():
            if n / max(pull.rows_raw, 1) > 0.02:
                notebook.note(
                    source="WPdx+", kind="duplication",
                    observed=f"{n:,} of {pull.rows_raw:,} returned records were removed: {reason}.",
                    handling="Removed before any coverage computation, per the cleaning "
                             "rules declared in the handbook.",
                    consequence="Coverage computed on the raw response would count these "
                                "records twice or count superseded observations.",
                )


def build(
    country: str,
    adm2: str,
    iso3: str,
    budget: int,
    ledger: Ledger,
    notebook: Notebook | None = None,
    gate=None,
    radius_m: float | None = None,
    grid_m: float | None = None,
    coarsen: int = 5,
    pad_deg: float = 0.03,
    min_separation_m: float | None = None,
    coverage_basis: str = "straight_line",
    minutes: float | None = None,
    objective: str = "max_coverage",
) -> Instance:
    if gate is not None:
        from .. import handbook as _hb
        env = {"openaq": "OPENAQ_API_KEY", "healthsites": "HEALTHSITES_API_KEY"}
        for key in ("wpdx", "worldpop"):
            hb = _hb.load(key)
            gate.source_access(hb.title, hb.needs_key, env.get(key))

    if radius_m is None:
        raise ValueError(
            "no service radius was supplied. What counts as served is a decision "
            "for a person, recorded in the decision register; this function will "
            "not assume one. See siting/decisions.py."
        )
    if grid_m is None:
        grid_m = radius_m * GRID_FRACTION_OF_RADIUS

    points = wpdx.fetch(country, adm2, ledger)
    if points.empty:
        raise RuntimeError(f"no water points for {adm2}, {country}")

    # The administrative boundary, if one can be matched by name. Without it the
    # area of interest would be an envelope around whatever happens to be
    # recorded, which admits neighbouring population into the denominator and
    # leaves the boundary check measuring the edge of a rectangle.
    boundary, match = None, None
    try:
        boundary, match = gadm.district(
            iso3, adm2, ledger,
            points_lat=points.lat_deg, points_lon=points.lon_deg)
    except (KeyError, ValueError) as exc:
        if notebook is not None:
            notebook.note(
                source="GADM", kind="coverage",
                observed=f"No administrative boundary could be matched for {adm2!r} "
                         f"in {iso3.upper()}: {str(exc)[:180]}",
                handling="The area of interest falls back to an envelope around the "
                         "retrieved records, padded by a fixed margin.",
                consequence="Population from neighbouring districts may be counted in "
                            "the denominator, and parts of the district with no recorded "
                            "points may be excluded. Coverage shares are for the envelope, "
                            "not the district.",
            )
    # A name match is not a unit correspondence. Verify before clipping to it.
    agree = None
    if boundary is not None:
        agree = gadm.agreement(points.lat_deg, points.lon_deg, boundary)
        if not agree["corresponds"]:
            if notebook is not None:
                notebook.note(
                    source="GADM", kind="coverage",
                    observed=f"The boundary matched by name for {adm2!r} does not "
                             f"correspond to the extent the register describes. "
                             f"{agree['verdict']}",
                    handling="The boundary was discarded and the area of interest falls "
                             "back to an envelope around the retrieved records. The "
                             "administrative boundary is not used for clipping, for the "
                             "denominator, or for the boundary-effect check.",
                    consequence="Coverage shares are for the envelope around the records, "
                                "not for the administrative district. Population from "
                                "neighbouring districts may be included and parts of the "
                                "district with no recorded points excluded.",
                )
            boundary = None

    if match is not None and notebook is not None and match.get("note"):
        notebook.note(
            source="GADM", kind="method",
            observed=f"The boundary for {adm2!r} was matched at administrative level "
                     f"{match['level']} within {match['within'] or 'the country'}. "
                     f"{match['note']}.",
            handling="The unit was located by name across levels rather than by "
                     "assuming the numbering of one source applies to the other.",
            consequence="Administrative level numbers are not comparable between the "
                        "register and the boundary source; the named unit is the same "
                        "place in both.",
        )

    if notebook is not None:
        _record_anomalies(points, notebook, ledger)

    if boundary is not None:
        bx = boundary.bounds
        bounds = (bx[0], bx[1], bx[2], bx[3])
    else:
        bounds = (
            points.lon_deg.min() - pad_deg,
            points.lat_deg.min() - pad_deg,
            points.lon_deg.max() + pad_deg,
            points.lat_deg.max() + pad_deg,
        )

    proj = utm_for(bounds)
    set_projection(proj)

    cells = worldpop.demand_grid(iso3, bounds, ledger, coarsen=coarsen)
    cells = cells.rename(columns={"pop": "weight"})

    outside_pop = 0.0
    if boundary is not None:
        keep = _inside(cells, boundary)
        outside_pop = float(cells.loc[~keep, "weight"].sum())
        cells = cells[keep].reset_index(drop=True)

    working = points[points.serving].rename(columns={"lat_deg": "lat", "lon_deg": "lon"})
    broken = points[~points.serving].rename(columns={"lat_deg": "lat", "lon_deg": "lon"})

    # Reach, on the basis the district chose.
    fr_arr = fr_tr = fr_cal = None
    if coverage_basis == "walking_time":
        fr_arr, fr_tr, _ = friction_src.fetch(bounds, ledger, surface="walking")
        fr_cal = friction_src.calibrate(fr_arr, radius_m)
        if minutes is None:
            minutes = fr_cal["median_minutes_at_radius"]
        if notebook is not None:
            notebook.note(
                source="MAP friction surface", kind="method",
                observed=f"Over this area the walking friction surface implies "
                         f"{fr_cal['median_minutes_per_km']:.0f} minutes per kilometre at "
                         f"the median and {fr_cal['p90_minutes_per_km']:.0f} at the 90th "
                         f"percentile, so the chosen {radius_m:.0f} m corresponds to about "
                         f"{minutes:.0f} minutes of walking.",
                handling="Coverage is tested as walking time rather than straight-line "
                         "distance. Reach is computed per candidate from the local "
                         "friction value rather than by a least-cost accumulation from "
                         "every candidate, which would be exact but is not tractable at "
                         "this candidate count.",
                consequence="Where terrain changes sharply within one facility's reach, "
                            "the local-friction approximation applies the conditions at "
                            "the site in every direction, and is conservative rather than "
                            "optimistic. The surface is modelled at about one kilometre "
                            "and does not represent local footpaths.",
            )

    def _cover(dem, cand):
        if coverage_basis == "walking_time":
            return coverage_by_travel_time(dem, cand, minutes, fr_arr, fr_tr)
        return coverage_matrix(dem, cand, radius_m, proj)

    baseline = _cover(cells, working[["lat", "lon"]].reset_index(drop=True)).any(axis=1) \
        if not working.empty else np.zeros(len(cells), dtype=bool)
    # The worst_case objective optimises this distance directly, so it has to be
    # measured on the same basis as coverage: minutes over the friction surface
    # when the district chose walking_time, not a straight line that ignores the
    # river or ridge the coverage figures already account for.
    baseline_d = (
        travel_minutes_to_nearest(cells, working[["lat", "lon"]], fr_arr, fr_tr)
        if coverage_basis == "walking_time" else
        distance_to_nearest(cells, working[["lat", "lon"]], proj)
    )

    # Candidates: rehabilitate a broken point, or drill somewhere new.
    rehab = broken[["lat", "lon", "clean_adm3", "water_tech_clean", "status_clean"]].copy()
    rehab["kind"] = "rehabilitate"
    rehab["existing_id"] = broken["row_id"].to_numpy()

    green = candidate_grid(bounds, grid_m, proj)
    green["clean_adm3"] = np.nan
    green["water_tech_clean"] = np.nan
    green["status_clean"] = np.nan
    green["existing_id"] = np.nan

    candidates = pd.concat([rehab, green], ignore_index=True)
    if boundary is not None:
        candidates = candidates[_inside(candidates, boundary)].reset_index(drop=True)

    # A greenfield site inside an existing service area adds nothing; drop it early
    # so the solver is not handed thousands of useless columns. Measured on the
    # same basis as coverage, for the same reason baseline_d is above: a
    # straight-line half-radius test would keep a candidate the walking_time
    # coverage matrix already treats as served, or drop one it does not.
    if coverage_basis == "walking_time":
        already_served = (
            coverage_by_travel_time(
                candidates, working[["lat", "lon"]].reset_index(drop=True),
                minutes * 0.5, fr_arr, fr_tr,
            ).any(axis=1)
            if not working.empty else np.zeros(len(candidates), dtype=bool)
        )
    else:
        already_served = served_today(candidates, working[["lat", "lon"]], radius_m * 0.5, proj)
    useful = ~already_served
    candidates = candidates[useful | (candidates.kind == "rehabilitate")].reset_index(drop=True)

    cover = _cover(cells, candidates)

    inst = Instance(
        demand=cells,
        candidates=candidates,
        cover=cover,
        budget=budget,
        domain=META["key"],
        objective=objective,
        coverage_rule=(
            f"within about {minutes:.0f} minutes of walking over the modelled terrain"
            if coverage_basis == "walking_time" else
            f"within {radius_m:.0f} m straight-line walking distance"),
        scope={
            "country": country,
            "adm2": adm2,
            "iso3": iso3.upper(),
            "radius_m": radius_m,
            "objective": objective,
            "objective_label": OBJECTIVE_META[objective]["label"],
            "objective_short": OBJECTIVE_META[objective]["short"],
            "coverage_basis": coverage_basis,
            "coverage_minutes": round(minutes, 1) if minutes else None,
            "friction_calibration": fr_cal,
            "grid_m": grid_m,
            "min_separation_m": (min_separation_m if min_separation_m is not None
                                 else radius_m * SEPARATION_FRACTION_OF_RADIUS),
            "separation_fraction": SEPARATION_FRACTION_OF_RADIUS,
            "bounds": [round(b, 5) for b in bounds],
            "points_total": int(len(points)),
            "points_working": int(len(working)),
            "points_broken": int(len(broken)),
            "points_at_risk": int(points.at_risk.sum()),
            "boundary": "administrative" if boundary is not None else "envelope",
            "boundary_match": match,
            "boundary_agreement": agree,
            "population_outside_boundary": round(outside_pop),
            "median_record_age_years": round(float(points.days_since_report.median()) / 365.25, 1),
            "grid_fraction": GRID_FRACTION_OF_RADIUS,
            "crs": proj.label,
            "crs_rationale": proj.rationale,
            "crs_check": crs_disagreement(
                cells["lat"].to_numpy(), cells["lon"].to_numpy(), proj),
        },
        baseline_covered=baseline,
        baseline_distance_m=baseline_d,
        friction_arr=fr_arr,
        friction_transform=fr_tr,
        boundary=boundary,
        projection=proj,
        min_separation_m=(min_separation_m if min_separation_m is not None
                          else radius_m * SEPARATION_FRACTION_OF_RADIUS),
    )
    return inst
