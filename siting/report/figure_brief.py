"""What each rendered figure claims, so the claim can be checked against the picture.

Every numerical check in this system reads `results.json`. None of them looks at
the maps, and a map can be wrong in ways no number reveals: a legend that covers
the data it describes, a scale bar computed from the wrong extent, a marker count
that does not match the table beside it, a colour ramp that has collapsed so that
the quantity it encodes is no longer readable.

This module pairs each figure with the assertions it must satisfy, drawn from the
run's own results. A reviewer with sight can then check the picture against the
account rather than against its own taste, which is the difference between asking
whether a map is attractive and asking whether it is true.

The reviewer is a separate agent with `Read` only. It cannot re-render, restyle
or negotiate, and it does not see how the figure was produced.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np


def _fmt(n: float | int) -> str:
    return f"{round(n):,}"


def build(doc: dict[str, Any], inst=None) -> dict[str, Any]:
    """Assemble the brief for one run's figures."""
    scope, base, plan = doc["scope"], doc["baseline"], doc["plan"]
    radius = float(scope.get("radius_m", 0))

    b = scope.get("bounds") or [0, 0, 0, 0]
    minx, miny, maxx, maxy = b
    lat_mid = (miny + maxy) / 2.0
    width_km = (maxx - minx) * 111.32 * float(np.cos(np.radians(lat_mid)))
    height_km = (maxy - miny) * 111.32

    figures = [
        {
            "file": "map_situation.png",
            "shows": "Recorded facilities and the population beyond the service radius",
            "assertions": [
                f"The legend lists exactly four entries: population, population beyond "
                f"the service radius, a serving facility marker, and a non-serving "
                f"facility marker.",
                f"Two marker styles are distinguishable: {_fmt(scope['points_working'])} "
                f"serving points drawn as filled circles and "
                f"{_fmt(scope['points_broken'])} non-serving points drawn as crosses. "
                f"Non-serving points should be the visibly sparser of the two.",
                f"Warm shading marks population beyond the service radius. It should "
                f"cover a substantial part of the populated area, because "
                f"{_fmt(base['uncovered'])} people of {_fmt(base['population'])}, "
                f"{1 - base['covered_share']:.0%} of the district, are unserved.",
                f"A scale bar and a north arrow are present and legible.",
                f"The map frame spans roughly {width_km:.0f} km east to west and "
                f"{height_km:.0f} km north to south. The scale bar should be "
                f"consistent with that: a bar labelled n km should be about "
                f"n/{max(width_km, 1):.0f} of the frame's width.",
                "No element obscures another: the legend must not sit over populated "
                "area or over facility markers, and the scale bar must not overlap data.",
            ],
        },
        {
            "file": "map_plan.png",
            "shows": "The recommended sites and their service radii",
            "assertions": [
                f"Exactly {len(plan['sites'])} recommended sites are drawn, each "
                f"numbered. The numbers should run 1 to {len(plan['sites'])} with none "
                f"missing or repeated.",
                f"Each recommended site carries a translucent circle showing its service "
                f"radius of {radius:.0f} m. On a frame about {width_km:.0f} km wide, each "
                f"circle should be roughly {200 * radius / 1000 / max(width_km, 1):.1f} "
                f"per cent of the frame's width across. Circles far larger or smaller "
                f"than that indicate the radius was drawn in the wrong units.",
                "Recommended sites should sit in or beside warm-shaded areas, since they "
                "were selected to reach unserved population. A site in the middle of an "
                "empty or already-served area is a finding.",
                f"The plan's minimum separation is "
                f"{scope.get('min_separation_m', 0):.0f} m, so no two numbered sites "
                f"should appear closer together than that.",
                "The legend distinguishes the recommended sites from the existing "
                "serving facilities.",
            ],
        },
        {
            "file": "fig_framework.png",
            "shows": "The four-slot analytical framework",
            "assertions": [
                "Four domain boxes on the left feed one problem instance in the middle, "
                "which feeds one solver on the right.",
                "The problem instance lists four slots: demand, candidates, coverage, "
                "budget.",
                "All text is legible at the size shown and no arrow crosses a label.",
            ],
        },
    ]

    return {
        "run": doc["run"]["id"],
        "context": {
            "district": f"{scope['adm2']}, {scope['country']}",
            "coverage_rule": scope["coverage_rule"],
            "objective": scope.get("objective", ""),
            "population": base["population"],
            "covered_before": base["covered"],
            "uncovered_before": base["uncovered"],
            "sites": len(plan["sites"]),
            "frame_km": [round(width_km, 1), round(height_km, 1)],
            "boundary": scope.get("boundary", ""),
        },
        "figures": figures,
        "how_to_answer": (
            "For each figure, check every assertion against the image. Report only "
            "what you can see. Where an assertion cannot be checked from the image, "
            "say so rather than guessing. A figure that is merely plain is not a "
            "finding; a figure that contradicts the account is."
        ),
    }


def write(doc: dict[str, Any], run_dir: Path) -> Path:
    out = run_dir / "figures.json"
    out.write_text(json.dumps(build(doc), indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def load_verdict(path: str | Path) -> dict[str, Any]:
    """Read a reviewer's verdict, refusing anything that is not one.

    A verdict that cannot be parsed is not treated as a pass. The figures either
    were reviewed or they were not, and the assessment says which.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"no figure review at {p}")
    d = json.loads(p.read_text(encoding="utf-8"))

    if "figures" not in d:
        raise ValueError("a figure review must carry a 'figures' list")
    for f in d["figures"]:
        for k in ("file", "verdict", "findings"):
            if k not in f:
                raise ValueError(f"figure entry {f.get('file', '?')} is missing {k!r}")
        if f["verdict"] not in ("accept", "revise", "unreadable"):
            raise ValueError(
                f"verdict for {f['file']} must be accept, revise or unreadable, "
                f"not {f['verdict']!r}")

    revise = [f for f in d["figures"] if f["verdict"] == "revise"]
    unread = [f for f in d["figures"] if f["verdict"] == "unreadable"]
    d["summary"] = {
        "reviewed": len(d["figures"]),
        "accepted": len(d["figures"]) - len(revise) - len(unread),
        "revise": len(revise),
        "unreadable": len(unread),
        "level": "flag" if (revise or unread) else "pass",
        "detail": (
            f"{len(d['figures'])} figures reviewed against the account; all consistent"
            if not (revise or unread) else
            f"{len(d['figures'])} figures reviewed; "
            + ", ".join(
                [f"{len(revise)} contradict the account or are hard to read"] if revise else []
                + [f"{len(unread)} could not be assessed"] if unread else []
            ) + ". " + "; ".join(
                f"{f['file']}: {'; '.join(f['findings'][:2])}"
                for f in revise + unread if f["findings"])
        ),
    }
    return d
