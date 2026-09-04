"""L6. Assemble the run directory and compile the brief."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import typst

TEMPLATE = Path(__file__).resolve().parent / "template"


FORMATS = {
    "bundle": ("main.typ", ("main.typ", "lib.typ"), "evidence-bundle.pdf"),
    "assessment": ("assessment.typ", ("assessment.typ", "gov_lib.typ"), "assessment.pdf"),
}


def prepare(run_dir: Path, fmt: str = "bundle") -> Path:
    """Copy the template for the requested format into the run directory, so the
    run carries the template it was produced with."""
    run_dir.mkdir(parents=True, exist_ok=True)
    _, files, _ = FORMATS[fmt]
    for f in files:
        shutil.copy(TEMPLATE / f, run_dir / f)
    return run_dir


def compile_brief(run_dir: Path, doc: dict[str, Any], fmt: str = "bundle") -> Path:
    (run_dir / "results.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    entry, _, name = FORMATS[fmt]
    out = run_dir / name
    typst.compile(str(run_dir / entry), output=str(out), root=str(run_dir))
    return out


def write_record(run_dir: Path, doc: dict[str, Any], manifest: dict[str, Any]) -> Path:
    """A plain-text index of the run, alongside the PDF, for anyone reading the
    repository rather than the bundle."""
    s = doc["scope"]
    lines = [
        f"# Siting run record: {s['adm2']}, {s['country']} ({s['domain']})",
        "",
        f"> {s['domain_title']}. Budget {s['budget']}. Generated {doc['run']['generated_at']}.",
        f"> Provenance hash `{doc['run']['provenance_hash']}`, manifest `{manifest['manifest_hash']}`.",
        "",
        "## Command",
        "",
        "```sh",
        doc.get("command", ""),
        "```",
        "",
        "## Decisions",
        "",
        (doc.get("authorship") or {}).get("statement", ""),
        "",
        "| Decision | Value | Recorded by | Reason |",
        "|---|---|---|---|",
    ] + [
        f"| `{dd['key']}` | {dd['value']} | "
        f"{'**the agent**' if dd.get('authored_by_agent') else dd['decided_by']} | "
        f"{dd['reason'].strip().replace(chr(10), ' ')} |"
        for dd in (doc.get("decisions") or [])
    ] + [
        "",
        "## Result",
        "",
        "| | |",
        "|---|---|",
        f"| Population in the area of interest | {doc['baseline']['population']:,} |",
        f"| Covered before | {doc['baseline']['covered']:,} ({doc['baseline']['covered_share']:.1%}) |",
        f"| Covered after | {doc['plan']['covered']:,} ({doc['plan']['covered_share']:.1%}) |",
        f"| Newly covered | {doc['plan']['newly_covered']:,} |",
        f"| Sites | {len(doc['plan']['sites'])} |",
        "",
        "## Exhibits",
        "",
        "| Ex | Content | Supports |",
        "|---|---|---|",
    ]
    for e in doc["exhibits"]:
        lines.append(f"| {e['id']} | {e['title']} | {e['supports']} |")

    lines += ["", "## Retrievals", "",
              "| Source | Retrieved | Read from | Raw | Clean | Licence |",
              "|---|---|---|---|---|---|"]
    for p in doc["provenance"]:
        lines.append(f"| {p['source']} | {p['fetched_at'][:10]} | "
                     f"{'local cache' if p.get('from_cache') else 'the source'} | "
                     f"{p['rows_raw']:,} | {p['rows_clean']:,} | {p['licence']} |")

    if doc.get("anomalies"):
        lines += ["", "## Anomalies recorded", ""]
        for i, a in enumerate(doc["anomalies"], 1):
            lines += [f"{i}. **{a['kind']} / {a['source']}** {a['observed']}",
                      f"   - Handling: {a['handling']}",
                      f"   - Bearing: {a.get('consequence', '')}"]

    if doc.get("audit"):
        lines += ["", "## Planner overrides", "",
                  "| Verb | Target | Cost in people | Reason |", "|---|---|---|---|"]
        for a in doc["audit"]:
            lines.append(f"| {a['verb']} | {a['target']} | {a['delta_covered']:+,} | {a['reason']} |")
        lines += ["", f"> {doc['guardrail']['verdict']}"]

    rv = doc.get("review")
    if rv:
        lines += ["", "## Scoring review", "",
                  f"Weighted **{rv['weighted']:.2f}** against a floor of "
                  f"{rv['accept_at']:.2f}: **{rv['decision']}**. "
                  f"Reviewer: {rv['reviewer']}.", "",
                  "| Dimension | Score | Floor | Weight | Outcome |",
                  "|---|---|---|---|---|"]
        for s in rv["scores"]:
            lines.append(f"| {s['dimension']} | {s['value']:.1f} | {s['floor']:.1f} | "
                         f"{s['weight']:.2f} | {'met' if s['passes'] else '**below floor**'} |")
        if rv.get("action_items"):
            lines += ["", "Outstanding:"] + [f"- {a}" for a in rv["action_items"]]

    gates = doc.get("gate_decisions", [])
    if gates:
        lines += ["", "## Gate decisions", "",
                  "| Gate | Outcome | Decided by | Reason |", "|---|---|---|---|"]
        for g in gates:
            lines.append(f"| {g['checkpoint']} | {'allowed' if g['allowed'] else '**refused**'} "
                         f"| {g['actor']} | {g['reason']} |")

    lines += ["", "## Independent checks", "", "| Check | Result | Finding |", "|---|---|---|"]
    for f in doc["evaluation"]:
        lines.append(f"| {f['check']} | {f['level'].upper()} | {f['detail']} |")

    out = run_dir / "RUN_RECORD.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
