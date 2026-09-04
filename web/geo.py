"""results.json to map layers, and the check that they describe the same place.

Four layers:

    boundary   the area of interest the run actually used
    broken     points the register says are not serving
    working    points it says are serving
    sites      the recommendation, in rank order, with its rationale

Only one of those is in `results.json`. The recommendation is; the boundary is
recorded as a GADM id, and the existing points only as counts. So this module
reads two sources back — the same GADM level the run matched at, and the same
cached WPdx query the run used — and then **counts what it drew and compares it
with what the account claims**.

That comparison is the point of the module. A map is the one part of an
assessment a reader believes without checking, and a map drawn from a second
retrieval can silently disagree with the document it illustrates: a boundary
from a different level, a point set from a different day. Every layer here
carries whether it reconciles, and a layer that does not is reported as such
rather than quietly drawn.

Population is not vectorised. The run already rendered `map_situation.png` and
that image is overlaid, which also keeps the payload small enough for a field
connection.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shapely.geometry import mapping

from siting.provenance import Ledger
from siting.sources import gadm, wpdx

# Degrees. About 30 m at this latitude: enough to halve the payload of a
# district outline, far below anything a reader could see at district scale.
SIMPLIFY = 0.0003


def _fc(features: list[dict]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _point(lat: float, lon: float, props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Point", "coordinates": [round(lon, 6),
                                                          round(lat, 6)]}}


def _boundary(doc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The polygon the run matched, by its own GADM id, or the envelope it used."""
    s = doc["scope"]
    match = s.get("boundary_match") or {}
    gid, level = match.get("gid"), match.get("level")
    west, south, east, north = s["bounds"]
    envelope = {
        "type": "Feature",
        "properties": {"kind": "envelope"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [west, south], [east, south], [east, north], [west, north],
            [west, south]]]},
    }

    if s.get("boundary") != "administrative" or not gid:
        return _fc([envelope]), {
            "layer": "boundary", "reconciles": True,
            "note": ("The run used an envelope around the records, not an "
                     "administrative boundary, and that is what is drawn."),
        }

    try:
        gdf = gadm.fetch_level(s["iso3"], int(level), Ledger())
        col = f"GID_{level}"
        row = gdf[gdf[col] == gid]
        if row.empty:
            raise KeyError(gid)
        geom = row.geometry.iloc[0].simplify(SIMPLIFY, preserve_topology=True)
        return _fc([{"type": "Feature",
                     "properties": {"kind": "administrative", "gid": gid,
                                    "name": match.get("matched")},
                     "geometry": mapping(geom)}]), {
            "layer": "boundary", "reconciles": True,
            "note": f"GADM level {level}, {gid}, the boundary the run matched.",
        }
    except Exception as exc:
        return _fc([envelope]), {
            "layer": "boundary", "reconciles": False,
            "note": (f"The run matched {gid} at GADM level {level}, but that "
                     f"polygon could not be read back ({type(exc).__name__}). "
                     "The bounding box is drawn instead, and it is not the "
                     "boundary the analysis used."),
        }


def _points(doc: dict[str, Any]) -> tuple[dict, dict, dict]:
    """The register, read back and counted against what the account claims."""
    s = doc["scope"]
    empty = _fc([]), _fc([])
    try:
        df = wpdx.fetch(s["country"], s["adm2"], Ledger())
    except Exception as exc:
        return (*empty, {"layer": "points", "reconciles": False,
                         "note": f"the register could not be read back: {exc}"})

    working, broken = [], []
    for row in df.itertuples():
        f = _point(row.lat_deg, row.lon_deg,
                   {"status": getattr(row, "status_clean", ""),
                    "serving": bool(row.serving)})
        (working if row.serving else broken).append(f)

    claimed = (s["points_total"], s["points_working"], s["points_broken"])
    drawn = (len(df), len(working), len(broken))
    ok = claimed == drawn
    return _fc(working), _fc(broken), {
        "layer": "points", "reconciles": ok,
        "note": (f"{drawn[0]:,} records drawn, {drawn[1]:,} serving — the same "
                 f"counts the assessment reports."
                 if ok else
                 f"The assessment reports {claimed[0]:,} records "
                 f"({claimed[1]:,} serving); this retrieval returned "
                 f"{drawn[0]:,} ({drawn[1]:,} serving). The map and the "
                 "document do not describe the same retrieval. Do not read the "
                 "map as illustrating the figures."),
    }


def _sites(doc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The recommendation. The one layer that comes wholly from the account."""
    feats = [
        _point(s["lat"], s["lon"], {
            "id": s["id"], "rank": s["rank"], "kind": s.get("kind"),
            "newly_covered": s["newly_covered"],
            "nearest_working_m": s.get("nearest_working_m"),
            "rationale": s.get("rationale", ""),
        })
        for s in doc["plan"]["sites"]
    ]
    return _fc(feats), {
        "layer": "sites", "reconciles": True,
        "note": f"{len(feats)} sites, read from results.json.",
    }


def layers(run_dir: str | Path) -> dict[str, Any]:
    """Everything the map draws, plus whether each layer reconciles."""
    run_dir = Path(run_dir)
    doc = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    s = doc["scope"]

    boundary, b_check = _boundary(doc)
    working, broken, p_check = _points(doc)
    sites, s_check = _sites(doc)
    checks = [b_check, p_check, s_check]

    situation = run_dir / "map_situation.png"
    plan_fig = run_dir / "map_plan.png"
    framework_fig = run_dir / "fig_framework.png"
    return {
        "scope": {"country": s["country"], "adm2": s["adm2"],
                  "radius_m": s["radius_m"],
                  "coverage_rule": s.get("coverage_rule", ""),
                  "crs": s.get("crs", "")},
        "bbox": s["bounds"],
        "layers": {"boundary": boundary, "working": working,
                   "broken": broken, "sites": sites},
        "checks": checks,
        "reconciles": all(c["reconciles"] for c in checks),
        "population_image": (f"/api/run-file/{run_dir.name}/map_situation.png"
                             if situation.exists() else None),
        "plan_image": (f"/api/run-file/{run_dir.name}/map_plan.png"
                       if plan_fig.exists() else None),
        "framework_image": (f"/api/run-file/{run_dir.name}/fig_framework.png"
                            if framework_fig.exists() else None),
        "table": [
            {"id": x["id"], "rank": x["rank"], "lat": x["lat"], "lon": x["lon"],
             "newly_covered": x["newly_covered"],
             "nearest_working_m": x.get("nearest_working_m"),
             "rationale": x.get("rationale", "")}
            for x in doc["plan"]["sites"]
        ],
        "plan": {"covered": doc["plan"]["covered"],
                 "newly_covered": doc["plan"]["newly_covered"],
                 "baseline_covered": doc["baseline"]["covered"],
                 "population": doc["baseline"]["population"]},
    }


def _main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m web.geo <run directory>")
        return 2
    out = layers(argv[0])
    print(f"{out['scope']['adm2']}, {out['scope']['country']}  "
          f"({out['scope']['crs']})")
    for name, fc in out["layers"].items():
        print(f"  {name:<10} {len(fc['features']):>5,} features")
    print()
    for c in out["checks"]:
        print(f"  {'ok  ' if c['reconciles'] else 'FLAG'} {c['layer']:<10} {c['note']}")
    print(f"\nmap and account reconcile: {out['reconciles']}")
    return 0 if out["reconciles"] else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
