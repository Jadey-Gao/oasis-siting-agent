"""Assemble the run into a numbered exhibit bundle.

The deliverable is not a designed report. It is an evidence package: a short
cover statement that argues a recommendation, and behind it a set of numbered
exhibits, each stating what was captured, from where, when, what it shows, and
how it was verified. Nothing in the cover statement is asserted without an
exhibit number after it.

Structure follows the convention used for filed evidence bundles: cover and
index first, exhibits numbered and self-contained, a verification method note,
and an explicit record of anomalies and corrections.
"""
from __future__ import annotations

from typing import Any


def _fmt(n: float | int) -> str:
    return f"{round(n):,}"


def _captured(p: dict[str, Any]) -> str:
    """Where the exhibit's data came from, and when it left the source.

    A run served from the local cache reads a file downloaded on an earlier date.
    Saying so is the difference between a retrieval date a reader can check and
    one that merely records when this process started.
    """
    s = f"{p['endpoint']} on {p['fetched_at'][:10]}"
    if p.get("from_cache"):
        s += ", read from the local cache for this run"
    return s


def build(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the ordered exhibit list. Every entry is renderable on its own."""
    scope, base, plan = doc["scope"], doc["baseline"], doc["plan"]
    prov = {p["source"]: p for p in doc["provenance"]}
    ex: list[dict[str, Any]] = []

    def add(num: str, title: str, supports: str, captured: str, method: str,
            blocks: list[dict[str, Any]]) -> None:
        ex.append({
            "id": f"Ex{num}",
            "num": num,
            "title": title,
            "supports": supports,
            "captured": captured,
            "method": method,
            "blocks": blocks,
        })

    # ---- Ex00  who decided what ------------------------------------------
    auth = doc.get("authorship") or {}
    decs = doc.get("decisions") or []
    if decs:
        add("00", "Decisions taken, and by whom",
            "That the judgements this assessment rests on are attributable",
            "Recorded before the analysis ran",
            "Each row is a judgement about values rather than a technical "
            "parameter: what counts as served, whose need the programme answers, "
            "whether a register of this age is fit to direct spending. The "
            "analysis refuses to run without them rather than assuming one, "
            "because assuming one would move the decision away from the officer "
            "accountable for it. Where a row is attributed to the agent, no "
            "district position was recorded and the agent decided in its absence.",
            [
                {"type": "note", "text": auth.get("statement", "")},
                {"type": "decisions", "rows": [
                    [d["key"],
                     str(d["value"]) + (f" {d['unit']}" if d.get("unit") else ""),
                     "AGENT" if d.get("authored_by_agent") else d["decided_by"],
                     d["reason"]] for d in decs]},
            ])

    # ---- Ex01  the register --------------------------------------------
    w = prov.get("WPdx+")
    if w:
        add("01", f"{scope['domain_title']} register for {scope['adm2']}",
            "The installed stock and its recorded condition",
            _captured(w),
            "Socrata query below, executed verbatim. Cleaning rules are declared "
            "in handbooks/wpdx.yaml and applied in order; each rule reports its "
            "own drop count.",
            [
                {"type": "query", "text": w["query"]},
                {"type": "counts", "rows": [
                    ["Records returned", _fmt(w["rows_raw"])],
                    ["Records after cleaning", _fmt(w["rows_clean"])],
                    ["Discarded", _fmt(w["dropped_total"])],
                ]},
                {"type": "drops", "rows": [[k, _fmt(v)] for k, v in w["drops"].items()]},
                {"type": "counts", "rows": [
                    ["Recorded as serving", _fmt(scope["points_working"])],
                    ["Recorded as not serving", _fmt(scope["points_broken"])],
                    ["Median record age", f"{scope['median_record_age_years']} years"],
                ]},
                {"type": "licence", "text": w["licence"]},
            ])

    # ---- Ex02  the demand surface --------------------------------------
    wp = prov.get("WorldPop")
    if wp:
        add("02", "Population surface",
            "The denominator for every coverage figure in this bundle",
            _captured(wp),
            "National 100 m grid, read over the area of interest and aggregated "
            "into blocks. The aggregation is coarser than the source and finer "
            "than the service radius, so it does not bias coverage.",
            [
                {"type": "query", "text": wp["query"]},
                {"type": "counts", "rows": [
                    ["Raster cells in window", _fmt(wp["rows_raw"])],
                    ["Aggregated demand cells", _fmt(wp["rows_clean"])],
                    ["Population in the area of interest", _fmt(base["population"])],
                ]},
                {"type": "note", "text": wp.get("note", "")},
                {"type": "licence", "text": wp["licence"]},
            ])

    # ---- Ex03  baseline -------------------------------------------------
    add("03", "Coverage before any intervention",
        "The size of the gap the recommendation is trying to close",
        "Computed from Ex01 and Ex02",
        f"A demand cell counts as covered when it lies {scope['coverage_rule']} "
        f"of a point recorded as serving. Coverage is the union over serving "
        f"points; a per-point sum would double count every settlement reachable "
        f"from two of them.",
        [
            {"type": "counts", "rows": [
                ["Population", _fmt(base["population"])],
                ["Covered today", f"{_fmt(base['covered'])}  ({base['covered_share']:.1%})"],
                ["Not covered", f"{_fmt(base['uncovered'])}  ({1 - base['covered_share']:.1%})"],
            ]},
            {"type": "image", "path": "map_situation.png", "height": "104mm",
             "caption": "Serving and non-serving points over the population surface. "
                        "Warm shading is population beyond the service radius."},
        ])

    # ---- Ex04  candidates ----------------------------------------------
    add("04", "Candidate sites considered",
        "That the recommendation was selected from a stated, reproducible set",
        "Constructed from Ex01 and the area of interest",
        "Two kinds of candidate: an existing point recorded as not serving, which "
        "could be rehabilitated, and a regular lattice of greenfield locations. "
        "Lattice points already inside an existing service area are removed before "
        "selection.",
        [
            {"type": "counts", "rows": [
                ["Candidates evaluated", _fmt(base["candidates"])],
                ["Lattice spacing", f"{scope.get('grid_m', 0):.0f} m"],
                ["Minimum spacing between recommendations", f"{scope.get('min_separation_m', 0):.0f} m"],
            ]},
        ])

    # ---- Ex05  the selection -------------------------------------------
    curve = doc["curve"]
    add("05", "Selection method and its guarantee",
        "That the ranking is reproducible and its optimality is bounded",
        "Computed",
        (plan.get("method", "") + " " + plan.get("guarantee", "")).strip() +
        " The exact integer programme is not used in the loop: it takes minutes at "
        "district scale where the heuristic takes seconds, and every planner "
        "override triggers a re-solve.",
        [
            {"type": "counts", "rows": [
                ["Sites selected", str(len(plan["sites"]))],
                ["Population newly covered", _fmt(plan["newly_covered"])],
                ["Coverage after the programme", f"{plan['covered_share']:.1%}"],
                ["Benchmark against an exact solver", _benchmark_line(doc)],
            ]},
            {"type": "curve", "rows": [
                [str(c["n"]), _fmt(c["marginal"]), _fmt(c["cumulative"]),
                 f"{c['cumulative_share']:.1%}"] for c in curve],
             "caption": (
                 f"Marginal and cumulative population covered. The first site "
                 f"reaches {_fmt(curve[0]['marginal'])} people, the last "
                 f"{_fmt(curve[-1]['marginal'])}."
             ) if curve else (
                 "No site was selected at this budget: the plan adds zero "
                 "facilities, so there is no marginal curve to show."
             )},
        ])

    # ---- Ex06  the recommendation --------------------------------------
    add("06", "Recommended sites",
        "The recommendation itself, in coordinates",
        "Computed",
        "Sites in selection order. Each row's coverage is additional to every row "
        "above it, so the programme must be built from the top down. Coordinates "
        "are WGS 84.",
        [
            {"type": "sites", "rows": [
                [str(s["rank"]), s["id"], s["kind"],
                 f"{s['lat']:.4f}, {s['lon']:.4f}",
                 _fmt(s["newly_covered"]),
                 "--" if s["nearest_working_m"] is None else f"{s['nearest_working_m'] / 1000:.1f} km"]
                for s in plan["sites"]]},
            {"type": "image", "path": "map_plan.png", "height": "104mm",
             "caption": "Recommended sites and their service radii over the unserved population."},
            {"type": "rationales", "rows": [[s["id"], s["rationale"]] for s in plan["sites"]]},
        ])

    # ---- Ex07  overrides ------------------------------------------------
    audit = doc["audit"]
    add("07", "Planner overrides and what they cost",
        "That the recommendation was reviewed by a person, and where it was overruled",
        "overrides YAML, reproduced verbatim below",
        "Each override was applied on its own and the plan re-solved, so every "
        "decision carries its own cost rather than one lumped figure. Overrides "
        "are never blocked. A veto removes an area, not a single lattice point, "
        "because a planner saying not there means a place.",
        ([{"type": "yaml", "text": doc.get("overrides_source", "")}] if doc.get("overrides_source") else [])
        + ([{"type": "audit", "rows": [
                [a["verb"], a["target"], a["reason"],
                 f"{a['delta_covered']:+,}"] for a in audit]},
            {"type": "verdict", "text": doc["guardrail"]["verdict"]}]
           if audit else
           [{"type": "note", "text": "No overrides were applied to this run. The "
                                     "recommendation in Ex06 is the unmodified output of Ex05."}]))

    # ---- Ex07b  gate decisions -------------------------------------------
    gates = doc.get("gate_decisions", [])
    if gates:
        add("07b", "Safety gate decisions",
            "That access, transfer and fallback decisions were taken deliberately",
            "Recorded at the point of each decision",
            "A gate never silently permits. Each row is a point at which the run "
            "either proceeded and recorded why that was safe, or refused. A refusal "
            "removes a source from the analysis and is stated here rather than left "
            "for the reader to infer from a missing figure.",
            [
                {"type": "gates", "rows": [
                    [g["checkpoint"], "allowed" if g["allowed"] else "refused",
                     g["actor"], g["reason"]] for g in gates]},
            ])

    # ---- Ex08  independent checks ---------------------------------------
    add("08", "Independent checks",
        "That the plan was checked by a process that did not produce it",
        "Computed",
        "These checks run in a separate process from the one that produced the "
        "plan, can reject it, and do not read the override reasons in Ex07. They "
        "check the plan against the data, not against the account given of it. A "
        "single rejection stops the bundle being produced.",
        [
            {"type": "checks", "rows": [
                [f["check"], f["level"], f["detail"]] for f in doc["evaluation"]]},
        ])

    # ---- Ex08b  scoring review -------------------------------------------
    rv = doc.get("review")
    if rv:
        add("08b", "Scoring review",
            "That the account was scored against a stated floor before issue",
            "Computed by a reviewer that reads only this bundle's results file",
            "The reviewer receives the results file and nothing else: not the "
            "solver, not the code path, not the override reasons. It cannot score "
            "the intention behind the plan, only the account given of it. Every "
            "dimension carries a floor and the weighted average carries a floor; "
            "the run is issued only when all are met, or when a person forces it "
            "and that is recorded.",
            [
                {"type": "review", "rows": [
                    [s["dimension"], f"{s['value']:.1f}", f"{s['floor']:.1f}",
                     f"{s['weight']:.2f}", "met" if s["passes"] else "BELOW FLOOR",
                     s["basis"]] for s in rv["scores"]]},
                {"type": "counts", "rows": [
                    ["Weighted score", f"{rv['weighted']:.2f}"],
                    ["Floor to issue", f"{rv['accept_at']:.2f}"],
                    ["Decision", rv["decision"]],
                    ["Reviewer", rv["reviewer"]],
                ]},
            ] + ([{"type": "note", "text": "This bundle was issued despite a score "
                                           "below the floor, on an explicit instruction."}]
                 if rv.get("forced") else [])
              + ([{"type": "note", "text": "Outstanding: " + "; ".join(rv["action_items"])}]
                 if rv.get("action_items") else []))

    # ---- Ex09  sensitivity ----------------------------------------------
    if doc.get("sensitivity"):
        add("09", "Sensitivity of the recommendation",
            "How far the recommendation depends on assumptions the planner can change",
            "Computed",
            "Each scenario re-solves from the same data with one assumption changed. "
            "A recommendation that survives every row is one a planner can defend.",
            [
                {"type": "sensitivity", "rows": [
                    [s["label"], _fmt(s["covered"]), f"{s['share']:.1%}",
                     f"{s['delta']:+,}"] for s in doc["sensitivity"]]},
            ])

    # ---- Ex10  anomalies and corrections --------------------------------
    anomalies = doc.get("anomalies", [])
    add("10", "Anomalies in the source data, and how they were handled",
        "That the source registers were read rather than assumed",
        "Observed during retrieval and cleaning",
        "Every item below is a property of the published data that would change a "
        "coverage figure if handled differently. Each is recorded with what was "
        "observed, what the agent did, and what a reader must keep in mind. None "
        "of these were corrected silently.",
        [
            {"type": "anomalies", "rows": [
                [a["kind"], a["source"], a["observed"], a["handling"], a.get("consequence", "")]
                for a in anomalies]}
            if anomalies else
            {"type": "note", "text": "No anomalies were recorded for this run."},
        ])

    # ---- Ex11  reproduction ----------------------------------------------
    add("11", "Reproduction record",
        "That this bundle can be regenerated from the recorded queries",
        "Computed",
        "The bundle is a function of one results file and the manifest that "
        "produced it. Any change to a source query, a cleaning rule or a "
        "retrieval date changes the provenance hash.",
        [
            {"type": "counts", "rows": [
                ["Run", doc["run"]["id"]],
                ["Generated", doc["run"]["generated_at"]],
                ["Provenance hash", doc["run"]["provenance_hash"]],
            ]},
            {"type": "command", "text": doc.get("command", "")},
            {"type": "sources", "rows": [
                [r["citation"], r["licence"], r["accessed"]] for r in doc["references"]]},
        ])

    return ex


def _benchmark_line(doc: dict[str, Any]) -> str:
    b = doc.get("benchmark") or {}
    if b.get("status") == "solved":
        return f"exact solver reaches {_fmt(b['covered'])} ({b['share']:.1%})"
    reason = b.get("reason", "")
    return f"not run ({b.get('status', 'unknown')}{'; ' + reason if reason else ''})"


def index_rows(exhibits: list[dict[str, Any]]) -> list[list[str]]:
    return [[e["id"], e["title"], e["supports"]] for e in exhibits]
