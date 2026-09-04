"""Entry point. One run produces one directory: results, manifest, figures, brief."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

from . import decisions as dec
from . import evaluate, results, review as review_mod, solve
from .compile import Instance
from .domains import water
from .harness import Gate, Handoff, Hooks, Stage
from .overrides import guardrail, load as load_overrides, price
from .provenance import Ledger, Notebook
from .report import build as report_build
from .report import figure_brief, maps

DOMAINS = {"water": water}


def _sensitivity(inst: Instance, base_covered: float) -> list[dict]:
    """The scenarios a planner will be asked about in a meeting.

    Each scenario changes one assumption and re-solves, under the objective the
    district recorded and on top of whatever the reviewing officer has already
    vetoed or pinned. Re-solving under a different objective, or discarding the
    officer's vetoes, would produce a table that compares the recommendation with
    something nobody proposed.
    """
    out = []

    # Rehabilitation only. Cheaper per site by roughly an order of magnitude, and
    # the base case never picks it, so the brief has to show what it costs.
    mask = (inst.candidates.kind == "rehabilitate").to_numpy()
    if mask.any():
        r = inst.variant(must_exclude=list(inst.must_exclude)
                         + [int(i) for i in np.where(~mask)[0]])
        rs = solve.solve(r)
        out.append({
            "label": "Rehabilitation of existing points only",
            "covered": round(rs.covered(r)),
            "share": round(rs.share(r), 4),
            "delta": round(rs.covered(r) - base_covered),
        })

    # Half the budget, and double it.
    for factor, label in ((0.5, "Half the budget"), (2.0, "Double the budget")):
        b = max(1, int(round(inst.budget * factor)))
        c = inst.variant(budget=b)
        s = solve.solve(c)
        out.append({
            "label": f"{label} ({b} sites)",
            "covered": round(s.covered(c)),
            "share": round(s.share(c), 4),
            "delta": round(s.covered(c) - base_covered),
        })

    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="siting", description="Open siting agent for local government")
    ap.add_argument("--country", required=True)
    ap.add_argument("--adm2", required=True)
    ap.add_argument("--iso3", required=True)
    ap.add_argument("--domain", default="water", choices=sorted(DOMAINS))
    ap.add_argument("--decisions", default=None,
                    help="YAML file recording the decisions that belong to a person")
    ap.add_argument("--mode", default="manual", choices=["manual", "auto"],
                    help="manual: stop until a person has recorded each value-laden "
                         "decision. auto: the agent decides, and every such decision "
                         "is attributed to the agent in the output")
    ap.add_argument("--budget", type=int, default=None,
                    help="shorthand for the budget decision; a reason is still "
                         "required for the record, so prefer --decisions")
    ap.add_argument("--overrides", default=None, help="YAML file of planner overrides")
    ap.add_argument("--out", default="runs")
    ap.add_argument("--benchmark", action="store_true", help="also solve exactly with spopt")

    ap.add_argument("--resume", default=None,
                    help="run id to resume; completed stages are not re-run")
    ap.add_argument("--reviewer", default="rules", choices=["rules", "llm"],
                    help="scoring reviewer; llm needs ANTHROPIC_API_KEY")
    ap.add_argument("--approve", action="append", default=[], metavar="CHECKPOINT=REASON",
                    help="supply a human approval for a gate that would otherwise refuse")
    ap.add_argument("--figure-review", default=None,
                    help="JSON verdict from the map-reviewer agent, folded into the "
                         "checks. Without it the assessment records that the figures "
                         "were not reviewed")
    ap.add_argument("--force-issue", action="store_true",
                    help="issue even if the reviewer scores below the floor, "
                         "recording in the output that it was forced")
    ap.add_argument("--format", dest="fmt", default="bundle",
                    choices=["bundle", "assessment", "both"],
                    help="bundle: numbered exhibit package; assessment: "
                         "government-style chaptered report")
    args = ap.parse_args(argv)

    domain = DOMAINS[args.domain]
    ledger, notebook = Ledger(), Notebook()

    # ---- decisions before anything else ----------------------------------- #
    try:
        register = dec.Register.load(args.decisions, mode=args.mode)
    except (FileNotFoundError, ValueError) as exc:
        print(f"decisions file: {exc}")
        return 2

    if args.budget is not None and not register.has("budget"):
        register.record("budget", args.budget,
                        "operator, on the command line",
                        "Supplied with --budget; no further reason was recorded.")

    try:
        register.require(*dec.REQUIRED_KEYS)
    except dec.Missing as exc:
        print(exc.report())
        return 4

    radius = float(register.get("service_radius_m"))
    objective = str(register.get("objective"))
    budget = int(register.get("budget"))
    tolerance = float(register.get("coverage_tolerance"))
    basis = str(register.get("coverage_basis"))
    if objective not in solve.OBJECTIVES:
        print(f"objective {objective!r} is not one of {sorted(solve.OBJECTIVES)}")
        return 2

    print(f"[decisions] mode {register.mode}: {register.summary()['statement']}")
    for d in register.to_list():
        who = "agent" if d["authored_by_agent"] else d["decided_by"]
        print(f"     {d['key']:<24} = {d['value']}   ({who})")

    # ---- harness ---------------------------------------------------------- #
    approvals = {}
    for a in args.approve:
        if "=" in a:
            k, v = a.split("=", 1)
            approvals[k.strip()] = v.strip()
    gate = Gate(approvals=approvals)

    if args.resume:
        run_dir = Path(args.out) / args.resume
        handoff = Handoff.load(run_dir)
        if handoff is None:
            print(f"no handoff.json under {run_dir}; cannot resume")
            return 2
        slug = args.resume
        print(f"[harness] resuming {slug} at stage {handoff.stage}; "
              f"completed: {', '.join(handoff.completed) or 'none'}")
    else:
        run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        slug = f"{args.adm2.lower()}-{args.domain}-{run_id}"
        run_dir = Path(args.out) / slug
        run_dir.mkdir(parents=True, exist_ok=True)
        handoff = Handoff(run_id=slug, generated_at=dt.datetime.now(
            dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), scope={
            "country": args.country, "adm2": args.adm2, "iso3": args.iso3,
            "domain": args.domain, "budget": args.budget,
            "overrides": args.overrides, "format": args.fmt,
        })
        handoff.write(run_dir)

    hooks = Hooks(run_dir, handoff, gate)

    # ---- L1 retrieve ------------------------------------------------------ #
    hooks.pre(Stage.RETRIEVE, f"{args.domain} for {args.adm2}, {args.country}")
    print(f"[L1] retrieving {args.domain} data for {args.adm2}, {args.country} ...")
    inst = domain.build(args.country, args.adm2, args.iso3, budget, ledger,
                        notebook=notebook, gate=gate, radius_m=radius,
                        coverage_basis=basis, objective=objective)
    hooks.post(Stage.RETRIEVE)

    for a in notebook:
        print(f"[L1] noted {a.kind}: {a.observed[:92]}")
    for r in gate.refusals:
        print(f"[gate] refused {r}")

    # ---- L2 compile ------------------------------------------------------- #
    hooks.pre(Stage.COMPILE)
    crs = inst.projection.label if inst.projection else "no projection"
    print(f"[L2] {inst.n_demand:,} demand cells, {inst.n_candidates:,} candidates, "
          f"{inst.total_weight:,.0f} people, {inst.baseline_share():.1%} covered today; "
          f"distances in {crs}")
    hooks.post(Stage.COMPILE)

    # ---- L3 solve --------------------------------------------------------- #
    hooks.pre(Stage.SOLVE)
    print(f"[L3] solving for {objective} ...")
    optimum = solve.solve(inst)
    base_covered = optimum.covered(inst)
    hooks.post(Stage.SOLVE)

    # ---- L4 human review -------------------------------------------------- #
    hooks.pre(Stage.REVIEW)
    ids = results.site_ids(optimum)
    overrides = load_overrides(args.overrides) if args.overrides else []
    diffs = []
    final = optimum
    if overrides:
        print(f"[L4] applying {len(overrides)} planner override(s) ...")
        # The instance the overrides were applied to replaces the original from
        # here on. A RESCOPE of the budget, and the officer's vetoes, have to reach
        # the checks, the sensitivity table and the report; carrying the pre-override
        # instance forward would have the budget check score the plan against a
        # budget nobody is working to.
        #
        # Each override's own site label is resolved inside `price()`, against
        # the plan as of just before that override — not against `ids` here,
        # which stays fixed to the original optimum purely so `sites_changed`
        # in the audit trail names a candidate the same way across every step.
        final, diffs, inst = price(inst, optimum, overrides, site_id=ids)
        ids = results.site_ids(final)

    guard = guardrail(optimum, final, inst, tolerance=tolerance)
    gate.guardrail(guard["breached"], guard["verdict"])
    print(f"[L4] {guard['verdict']}")
    hooks.post(Stage.REVIEW)

    # What each figure claims, so a reviewer with sight can check the picture
    # against the account rather than against its own taste.
    fig_review = None
    if args.figure_review:
        try:
            fig_review = figure_brief.load_verdict(args.figure_review)
            s = fig_review["summary"]
            print(f"[L6] figure review: {s['accepted']} accepted, {s['revise']} to revise, "
                  f"{s['unreadable']} unreadable")
            for f in fig_review["figures"]:
                if f["verdict"] != "accept":
                    for finding in f["findings"]:
                        print(f"     {f['file']}: {finding}")
        except (FileNotFoundError, ValueError) as exc:
            print(f"[L6] figure review rejected: {exc}")
            return 2

    # ---- L5 independent checks -------------------------------------------- #
    hooks.pre(Stage.EVALUATE)
    print("[L5] independent checks ...")
    findings, may_publish = evaluate.run(inst, final, ledger,
                                         figure_review=fig_review, register=register)
    for f in findings:
        print(f"     {f.level.upper():<6} {f.check}: {f.detail}")
    if not may_publish:
        rejected = [f for f in findings if f.level == "reject"]
        print("[L5] a check rejected the plan; nothing issued")
        for f in rejected:
            print(f"     {f.check}: {f.detail}")
        # A refusal recorded in the decisions file is the officer's, not the
        # tool's, and --force-issue does not reach it: that flag exists to let a
        # person overrule the scoring reviewer, not to overrule themselves.
        hooks.stop()
        return 2
    hooks.post(Stage.EVALUATE)

    # The equity question only becomes real once there is a plan to ask it about.
    equity = next((f for f in findings if f.check == "equity"), None)
    if equity is not None and not register.has("equity_accepted"):
        if register.mode == "auto":
            register.require("equity_accepted")
        else:
            spec = register.spec("equity_accepted")
            print()
            print("[checkpoint] equity_accepted")
            print(f"     measured: {equity.detail}")
            print(f"     {spec.question}")
            print(f"     options: {spec.options_hint}")
            print("     This is recorded as unresolved. Add equity_accepted to the "
                  "decisions file to settle it.")
            register.record("equity_accepted", "unresolved",
                            "not settled at run time",
                            "The measured distribution was reported to the operator and "
                            "no position was recorded, so the assessment states it as "
                            "unresolved rather than accepted.")

    bench = solve.benchmark(inst) if args.benchmark else {"status": "not run"}
    sens = _sensitivity(inst, base_covered)

    from .sources import wpdx as _wpdx
    points = _wpdx.fetch(args.country, args.adm2, Ledger())
    working = points[points.serving].rename(columns={"lat_deg": "lat", "lon_deg": "lon"})
    broken = points[~points.serving].rename(columns={"lat_deg": "lat", "lon_deg": "lon"})
    credit = "Sources: WPdx+ (CC BY 4.0); WorldPop (CC BY 4.0). Coordinates WGS 84."
    # Rendered every run rather than gated on the stage. The figures have to
    # depict the plan that is in memory now, and a resume has just recomputed it.
    # They are produced before the review gate on purpose: a run that scores below
    # the floor still leaves its figures and figures.json behind, which is what the
    # map-reviewer needs in order to be run on them at all.
    print("[L6] rendering figures ...")
    maps.situation(inst, working, broken, run_dir / "map_situation.png", credit)
    maps.plan(inst, final, working, run_dir / "map_plan.png", credit, ids)
    maps.framework(run_dir / "fig_framework.png",
                   inst.scope.get("objective_short"))

    cmd = " ".join(["python", "-m", "siting.cli", *argv])
    ov_src = Path(args.overrides).read_text(encoding="utf-8") if args.overrides else ""
    doc = results.build(
        inst, final, optimum, ledger, findings, diffs, guard,
        domain_meta=domain.META, benchmark=bench, sensitivity=sens,
        working=working, run_id=slug, generated_at=handoff.generated_at,
        notebook=notebook,
        overrides_source=ov_src, command=cmd, gate=gate, register=register,
        figure_review=fig_review,
    )
    figure_brief.write(doc, run_dir)
    mf = results.manifest(doc, ["python", "-m", "siting.cli", *argv], args.overrides)
    (run_dir / "manifest.json").write_text(json.dumps(mf, indent=2), encoding="utf-8")
    results.write(doc, run_dir / "results.json")

    # ---- scoring reviewer, isolated context ------------------------------- #
    if hooks.pre(Stage.SCORE):
        rv = review_mod.review(run_dir / "results.json", reviewer=args.reviewer)
    else:
        prior = handoff.review_score or {}
        rv = review_mod.Review(
            scores=[review_mod.Score(s["dimension"], s["value"], s["weight"],
                                     s["floor"], s["basis"]) for s in prior.get("scores", [])],
            weighted=prior.get("weighted", 0.0),
            accept_at=prior.get("accept_at", review_mod.ACCEPT_AT),
            decision=prior.get("decision", "revise"),
            reviewer=prior.get("reviewer", "restored from handoff"),
            action_items=prior.get("action_items", []),
        )
    print(f"[review] {rv.reviewer}")
    for s in rv.scores:
        mark = "ok   " if s.passes else "FLOOR"
        print(f"     {mark} {s.dimension:<16} {s.value:>4.1f} / floor {s.floor:>3.1f}  {s.basis[:74]}")
    print(f"[review] weighted {rv.weighted:.2f} against a floor of "
          f"{rv.accept_at} recorded for this district -> {rv.decision}")
    for item in rv.action_items:
        print(f"     action: {item}")

    handoff.review_score = rv.to_dict()
    doc["review"] = rv.to_dict()
    if not rv.may_issue and not args.force_issue:
        results.write(doc, run_dir / "results.json")
        handoff.write(run_dir)
        hooks.stop()
        print(f"[review] not issued: {rv.why_not_issued}. Re-run with "
              f"--force-issue to issue anyway, which is recorded in the output.")
        return 3
    if not rv.may_issue:
        doc["review"]["forced"] = True
        print(f"[review] issued on --force-issue despite: {rv.why_not_issued}; "
              f"recorded in the output")

    from . import exhibits as _ex
    doc["exhibits"] = _ex.build(doc)
    results.write(doc, run_dir / "results.json")
    if Stage.SCORE.value not in handoff.completed:
        hooks.post(Stage.SCORE)

    # ---- L6 render -------------------------------------------------------- #
    # What is on disk goes stale, not the stage. Skipping on stage completion let
    # a resumed run rewrite results.json and leave the previous compilation of it
    # beside the new file, so the two documents this system promises cannot
    # disagree did exactly that. The decision is now made on the content.
    fmts = ("bundle", "assessment") if args.fmt == "both" else (args.fmt,)
    render_key = {"results_hash": results.content_hash(doc), "formats": sorted(fmts)}
    prior = handoff.rendered or {}
    on_disk = [Path(v) for k, v in handoff.artifacts.items()
               if k.startswith("document_")]

    if prior.get("formats") != render_key["formats"]:
        why = (f"{', '.join(sorted(fmts))} requested, "
               f"{', '.join(prior.get('formats') or ['nothing'])} on disk")
    elif prior.get("results_hash") != render_key["results_hash"]:
        why = "results.json has changed since the documents were compiled"
    elif not on_disk or not all(p.exists() for p in on_disk):
        why = "a compiled document is missing from the run directory"
    else:
        why = ""

    if hooks.pre(Stage.RENDER, note=why, force=bool(why)):
        outs = []
        for f in fmts:
            report_build.prepare(run_dir, f)
            print(f"[L6] compiling {f} ...")
            outs.append(report_build.compile_brief(run_dir, doc, fmt=f))
        report_build.write_record(run_dir, doc, mf)
        handoff.rendered = render_key
        hooks.post(Stage.RENDER, **{f"document_{i}": str(o) for i, o in enumerate(outs)})
    else:
        print("[render] the documents on disk were compiled from this exact "
              "results.json; skipped")
        outs = on_disk

    if hooks.pre(Stage.ISSUE):
        hooks.post(Stage.ISSUE)
    hooks.stop()

    print(f"[L7] manifest {mf['manifest_hash']}")
    for o in outs:
        print(f"     {o}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
