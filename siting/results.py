"""The JSON contract between the analysis and the report.

Typst reads this file directly, so the report is a pure function of it. That
makes the document reproducible from a manifest and means the template can be
built and reviewed against invented data before any adapter exists.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .compile import Instance
from .evaluate import Finding
from .overrides import Diff
from .provenance import Ledger, Notebook
from .solve import OBJECTIVE_META, Solution, site_ids, worst_case_description

# `site_ids` re-exported from `solve.py` — see the docstring there for why it
# lives there rather than here.


def _nearest_existing_m(inst: Instance, cand_idx: int, working: pd.DataFrame) -> float | None:
    if working.empty:
        return None
    lat = float(inst.candidates.lat.iloc[cand_idx])
    lon = float(inst.candidates.lon.iloc[cand_idx])
    if inst.projection is not None:
        from .spatial import to_projected
        sx, sy = to_projected(np.array([lat]), np.array([lon]), inst.projection)
        wx, wy = to_projected(working.lat.to_numpy(float), working.lon.to_numpy(float), inst.projection)
        return float(np.min(np.hypot(wx - sx[0], wy - sy[0])))
    from .clean import haversine_m
    d = haversine_m(lat, lon, working.lat.to_numpy(float), working.lon.to_numpy(float))
    return float(np.min(d))


def _rationale(row: pd.Series, gain: float, nearest: float | None) -> str:
    what = "Rehabilitate the existing point" if row.get("kind") == "rehabilitate" else "New site"
    bits = [f"{what} covering {gain:,.0f} people currently beyond the service radius"]
    if row.get("kind") == "rehabilitate" and pd.notna(row.get("status_clean")):
        bits.append(f"recorded as {str(row['status_clean']).lower()}")
    if pd.notna(row.get("water_tech_clean")):
        bits.append(f"technology on record: {row['water_tech_clean']}")
    if nearest is not None:
        bits.append(f"nearest working point {nearest / 1000:.1f} km away")
    return "; ".join(bits) + "."


def build(
    inst: Instance,
    sol: Solution,
    optimum: Solution,
    ledger: Ledger,
    findings: list[Finding],
    diffs: list[Diff],
    guard: dict[str, Any],
    domain_meta: dict[str, str],
    benchmark: dict[str, Any] | None = None,
    sensitivity: list[dict[str, Any]] | None = None,
    working: pd.DataFrame | None = None,
    run_id: str = "",
    generated_at: str = "",
    notebook: Notebook | None = None,
    overrides_source: str = "",
    command: str = "",
    gate=None,
    register=None,
    figure_review=None,
) -> dict[str, Any]:
    ids = site_ids(sol)
    working = working if working is not None else pd.DataFrame(columns=["lat", "lon"])

    sites = []
    for n, c in enumerate(sol.sites):
        row = inst.candidates.iloc[c]
        nearest = _nearest_existing_m(inst, c, working)
        sites.append({
            "id": ids[c],
            "rank": n + 1,
            "candidate": int(c),
            "lat": round(float(row.lat), 6),
            "lon": round(float(row.lon), 6),
            "kind": str(row.get("kind", "greenfield")),
            "adm3": None if pd.isna(row.get("clean_adm3")) else str(row.get("clean_adm3")),
            "newly_covered": round(sol.marginal[n]),
            "nearest_working_m": None if nearest is None else round(nearest),
            "rationale": _rationale(row, sol.marginal[n], nearest),
        })

    base_share = inst.baseline_share()
    doc = {
        "run": {
            "id": run_id or dt.datetime.now(dt.timezone.utc).strftime("run-%Y%m%dT%H%M%SZ"),
            # Fixed for the life of the run and carried across a resume, so a
            # bundle does not claim two different generation times on two
            # compilations of the same analysis.
            "generated_at": generated_at or dt.datetime.now(dt.timezone.utc)
                                              .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "provenance_hash": ledger.hash(),
            "agent": "OASIS siting agent",
        },
        "scope": {
            **inst.scope,
            "domain": inst.domain,
            "domain_title": domain_meta.get("title", inst.domain),
            "unit": domain_meta.get("unit", "facility"),
            "budget": inst.budget,
            "coverage_rule": inst.coverage_rule,
            "objective": inst.scope.get("objective", ""),
        },
        "baseline": {
            "population": round(inst.total_weight),
            "covered": round(inst.total_weight * base_share),
            "uncovered": round(inst.total_weight * (1 - base_share)),
            "covered_share": round(base_share, 4),
            "demand_cells": inst.n_demand,
            "candidates": inst.n_candidates,
        },
        "plan": {
            "sites": sites,
            "covered": round(sol.covered(inst)),
            "covered_share": round(sol.share(inst), 4),
            "newly_covered": round(sol.covered(inst) - inst.total_weight * base_share),
            "guarantee": sol.guarantee,
            # How the plan was arrived at, in the words the report uses. Written
            # once here so the cover statement, Ex05, the assessment's method
            # chapter and the scoring reviewer cannot describe it differently.
            # worst_case's method text depends on the population-weighting
            # exponent, which a REWEIGHT override may have changed from the
            # 0.5 default, and on the coverage basis, so it cannot come from
            # the static OBJECTIVE_META text.
            "method": (
                worst_case_description(
                    inst.equity_exponent, inst.scope.get("coverage_basis", "straight_line")
                )["selection"]
                if inst.objective == "worst_case"
                else OBJECTIVE_META[inst.objective]["selection"]
            ),
            "objective_label": OBJECTIVE_META[inst.objective]["label"],
            "notes": sol.notes,
        },
        "curve": sol.curve(inst),
        "guardrail": guard,
        "audit": [d.to_dict() for d in diffs],
        "evaluation": [f.to_dict() for f in findings],
        "provenance": ledger.to_list(),
        "benchmark": benchmark or {"status": "not run"},
        "sensitivity": sensitivity or [],
        "references": _references(ledger),
        "anomalies": notebook.to_list() if notebook is not None else [],
        "overrides_source": overrides_source,
        "command": command,
        "gate_decisions": gate.to_list() if gate is not None else [],
        "decisions": register.annotated() if register is not None else [],
        "figure_review": figure_review,
        "authorship": register.summary() if register is not None else {},
    }
    from . import exhibits
    doc["exhibits"] = exhibits.build(doc)
    return doc


def _references(ledger: Ledger) -> list[dict[str, str]]:
    """Every source this run read, with what a reader should cite for it.

    Driven by the ledger rather than by the handbook directory, for two reasons.
    The bibliography should list what the run actually used, not what the
    repository happens to carry. And a pull is matched to its handbook by the key
    the retrieving module recorded, not by testing whether one prose string is a
    substring of another: that test paired three sources by coincidence and
    dropped the fourth, silently, so the walking-time basis of every coverage
    figure went uncited.

    A pull naming no handbook still appears, described by its own record. A
    source that was read and cannot be cited is a fact the reader needs, not a
    row to leave out.
    """
    from . import handbook

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for pull in ledger:
        if pull.source in seen:
            continue
        seen.add(pull.source)
        if pull.handbook:
            hb = handbook.load(pull.handbook)
            citation, licence = hb.citation, hb.licence
        else:
            citation = f"{pull.source}, retrieved from {pull.endpoint}"
            licence = pull.licence or "not recorded"
        out.append({
            "key": pull.handbook or pull.source,
            "citation": citation,
            "licence": licence,
            "accessed": pull.fetched_at[:10],
        })
    return out


def content_hash(doc: dict[str, Any]) -> str:
    """A hash of the document a report would be compiled from.

    The render stage skips only when the documents on disk were compiled from
    exactly this content. Keying that decision on stage completion instead let a
    resumed run rewrite results.json and leave the previous run's PDF beside it,
    which is precisely what compiling both documents from one results file was
    supposed to make impossible.
    """
    return hashlib.sha256(
        json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


def write(doc: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def manifest(doc: dict[str, Any], argv: list[str], overrides_path: str | None) -> dict[str, Any]:
    """L7. Replaying this reproduces the same PDF."""
    payload = {
        "run_id": doc["run"]["id"],
        "generated_at": doc["run"]["generated_at"],
        "command": argv,
        "scope": doc["scope"],
        "overrides_file": overrides_path,
        "provenance_hash": doc["run"]["provenance_hash"],
        "sources": [
            {"source": p["source"], "query": p["query"], "fetched_at": p["fetched_at"],
             "rows_clean": p["rows_clean"], "from_cache": p.get("from_cache", False)}
            for p in doc["provenance"]
        ],
    }
    # The hash covers what the run was given, not when it happened to run. A
    # manifest whose hash moved on every replay could not be used to check that a
    # replay had in fact reproduced the bundle, which is the only thing it is for.
    # `run_id` and `generated_at` stay in the manifest to be read: they are this
    # run's identity, not part of its inputs.
    reproducible = {k: v for k, v in payload.items()
                    if k not in ("run_id", "generated_at")}
    payload["manifest_hash"] = hashlib.sha256(
        json.dumps(reproducible, sort_keys=True).encode()
    ).hexdigest()[:16]
    return payload
