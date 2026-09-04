"""What `web/` assumes about `siting/`, written down and checkable.

The web layer reads from `siting/` and calls its CLI, and never modifies either.
That isolation is one-directional: a change inside `siting/` can still break the
web layer, and the ways it can do so are not equally visible.

    a renamed flag        the run crashes and someone notices immediately
    a reworded print      the log renders a little worse; nothing breaks
    a changed exit code   the web layer misreports the outcome, silently

The third is why this file exists. A run that scored below the review floor
being shown as issued is exactly the failure this project is built to prevent,
and it would not announce itself. So every assumption is stated here as a check
that either passes or names what changed.

    python -m web.contract          the cheap checks, about a second
    python -m web.contract --full   also runs the CLI to confirm its exit codes

Run it after changing anything in `siting/`. This file is allowed to know things
about `siting/`; nothing else in `web/` is allowed to know things this file does
not check.
"""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from siting import decisions as dec
from siting import harness, overrides as ov

from . import emit, runner
from .runner import REPO

OK, WARN, BAD = "ok", "warn", "BROKEN"


@dataclass
class Result:
    status: str
    check: str
    detail: str


def _spec_hint(key: str) -> str:
    for r in dec.REGISTER + dec.DEFERRED:
        if r.key == key:
            return r.options_hint
    return ""


def _r(cond: bool, check: str, ok: str, bad: str, soft: bool = False) -> Result:
    if cond:
        return Result(OK, check, ok)
    return Result(WARN if soft else BAD, check, bad)


# --- the register ------------------------------------------------------- #

def check_register() -> list[Result]:
    """`questions.py` and `emit.py` read the register rather than restating it."""
    out = []
    fields = {"key", "question", "why_not_a_default", "unit", "options_hint"}
    missing = fields - set(dec.REGISTER[0].__dataclass_fields__)
    out.append(_r(not missing, "register fields",
                  f"Required carries {', '.join(sorted(fields))}",
                  f"Required no longer carries {', '.join(sorted(missing))}; "
                  "question cards would lose that text"))

    out.append(_r(bool(dec.REQUIRED_KEYS) and dec.REQUIRED_KEYS <= emit.KEYS,
                  "register keys",
                  f"{len(dec.REQUIRED_KEYS)} required, {len(dec.DEFERRED)} deferred, "
                  "all writable",
                  "emit.KEYS no longer covers REQUIRED_KEYS"))

    # The one thing the register does not declare is a type. emit.NUMERIC
    # supplies it, so a new numeric decision has to be added there or it will be
    # written to YAML as a string.
    stray = set(emit.NUMERIC) - emit.KEYS
    out.append(_r(not stray, "emit.NUMERIC keys exist",
                  "every coerced key is a real decision",
                  f"emit.NUMERIC names decisions that no longer exist: {sorted(stray)}"))

    numericish = {r.key for r in dec.REGISTER
                  if r.unit and r.key not in emit.NUMERIC
                  and any(w in r.unit for w in ("metre", "share", "facilit", "minute"))}
    out.append(_r(not numericish, "emit.NUMERIC coverage",
                  "no decision looks numeric without being coerced",
                  f"these carry a numeric unit but are not in emit.NUMERIC and would "
                  f"be written as strings: {sorted(numericish)}", soft=True))

    # `questions.py` reads `options_hint`, which is prose a person wrote, as the
    # source of the qualitative options and of each value's adjective. It is the
    # only place the web layer parses prose, so a change in how the hints are
    # written shows up here rather than as options quietly going missing.
    from . import questions as q
    unsplit = [r.key for r in dec.REGISTER + dec.DEFERRED
               if r.options_hint and len(q._hint_options(r.options_hint)) < 2]
    out.append(_r(not unsplit, "options_hint splits",
                  "every hint with options still separates on '|'",
                  f"these hints no longer split into options: {unsplit}", soft=True))

    lost = []
    for key, values in __import__("web.pricing", fromlist=["OPTIONS"]).OPTIONS.items():
        hint = _spec_hint(key)
        lost += [f"{key}={v}" for v in values if not q._note_for(v, hint)]
    out.append(_r(not lost, "options_hint covers priced values",
                  "every probed value is named in the register's hint",
                  f"probed values the hint no longer names, so they lose their "
                  f"adjective on the card: {lost}", soft=True))

    for name in ("load", "has", "require", "spec", "record", "to_list", "summary"):
        out.append(_r(hasattr(dec.Register, name), f"Register.{name}",
                      "present", f"Register.{name} is gone; emit or questions uses it"))
    return out


# --- the four verbs ------------------------------------------------------ #

def check_overrides() -> list[Result]:
    """`emit.write_overrides` builds a real Override so a bad one cannot reach disk."""
    out = []
    out.append(_r(set(ov.VERBS) == {"VETO", "PIN", "REWEIGHT", "RESCOPE"},
                  "the four verbs", f"{', '.join(ov.VERBS)}",
                  f"the verb set changed to {ov.VERBS}; claude.classify's "
                  "enumeration and the override card both name them"))

    fields = set(ov.Override.__dataclass_fields__)
    written = {"verb", "actor", "reason", "ts", "site", "candidate",
               "equity_weight", "radius_m", "budget", "veto_radius_m"}
    gone = written - fields
    out.append(_r(not gone, "override fields",
                  "every field emit writes still exists",
                  f"emit writes fields Override no longer accepts: {sorted(gone)}"))

    try:
        ov.Override(verb="VETO", reason="")
        rejects_blank = False
    except ValueError:
        rejects_blank = True
    out.append(_r(rejects_blank, "a reason is mandatory",
                  "Override still refuses a blank reason",
                  "Override accepts a blank reason; emit's guarantee now rests on "
                  "emit alone"))
    return out


# --- the pipeline -------------------------------------------------------- #

def check_stages() -> list[Result]:
    """`runner.STAGES` mirrors the harness, and the log's tags still appear."""
    out = []
    actual = tuple(s.value for s in harness.Stage.order())
    out.append(_r(actual == runner.STAGES, "stage order",
                  " -> ".join(actual),
                  f"harness order is {actual}, runner.STAGES is {runner.STAGES}; "
                  "the progress rail would be wrong"))

    src = (REPO / "siting" / "cli.py").read_text(encoding="utf-8")
    tags = set(runner.STAGE_BY_TAG) | set(runner.KIND_BY_TAG)
    absent = sorted(t for t in tags if t not in src)
    out.append(_r(not absent, "log tags",
                  f"all {len(tags)} tags still printed",
                  f"cli.py no longer prints {absent}; those lines fall back to "
                  "plain log lines, which is a degraded view, not a failure",
                  soft=True))
    return out


# --- the command line ---------------------------------------------------- #

def check_flags() -> list[Result]:
    """Every flag `RunSpec.command()` can emit must still be accepted."""
    spec = runner.RunSpec(
        country="X", adm2="Y", iso3="ZZZ", out_dir=Path("/tmp/none"),
        decisions=Path("/tmp/d.yaml"), overrides=Path("/tmp/o.yaml"),
        budget=1, figure_review=Path("/tmp/f.json"), force_issue=True,
    )
    flags = [a for a in spec.command() if a.startswith("--")]
    help_text = subprocess.run(
        [sys.executable, "-m", "siting.cli", "--help"],
        cwd=str(REPO), capture_output=True, text=True, timeout=60,
    ).stdout
    absent = [f for f in flags if f not in help_text]
    return [_r(not absent, "cli flags",
               f"all {len(flags)} flags accepted",
               f"the CLI no longer accepts {absent}; every run would fail")]


# --- results.json -------------------------------------------------------- #

# Field paths the web layer reads. Anything added to a page must be added here,
# or a rename in siting/results.py goes unnoticed until a demonstration.
READS = (
    "run.id", "scope.adm2", "scope.country", "scope.budget",
    "baseline.population", "baseline.covered", "baseline.covered_share",
    "plan.sites", "plan.covered", "plan.newly_covered",
    "review.weighted", "review.accept_at", "review.decision",
    "guardrail.people_forgone", "guardrail.verdict",
    "evaluation", "provenance", "anomalies", "decisions", "authorship",
)


def _dig(doc: dict, path: str):
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def check_results() -> list[Result]:
    """Read against a sample run, which is committed and always present."""
    samples = sorted((REPO / "sample-runs").glob("*/results.json"))
    if not samples:
        return [Result(WARN, "results.json fields",
                       "no sample run to check against")]
    doc = json.loads(samples[0].read_text(encoding="utf-8"))
    absent = [p for p in READS if _dig(doc, p) is None]
    return [_r(not absent, "results.json fields",
               f"all {len(READS)} paths present in {samples[0].parent.name}",
               f"missing from {samples[0].parent.name}: {absent}")]


# --- exit codes, the one that fails silently ----------------------------- #

def check_exit_codes(full: bool) -> list[Result]:
    """Confirm the CLI still means what `runner.Outcome` says it means.

    The two cheap cases need no data. The two expensive ones do, so they run
    only under --full.
    """
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # 4: decisions outstanding. One recorded, five not.
        partial = tmp / "partial.yaml"
        partial.write_text(
            "decisions:\n"
            "  - key: service_radius_m\n    value: 1000\n"
            "    decided_by: contract check\n"
            "    reason: recorded alone so the refusal can be observed\n",
            encoding="utf-8")
        code = _exit(["--decisions", str(partial), "--out", str(tmp / "runs")])
        out.append(_r(code == runner.Outcome.DECISIONS_OUTSTANDING,
                      "exit 4 = decisions outstanding",
                      "an incomplete register still refuses to run",
                      f"an incomplete register now exits {code}, not 4; the "
                      "interface would not know to go back and ask"))

        # 2: unusable input.
        code = _exit(["--decisions", str(tmp / "absent.yaml"), "--out", str(tmp / "runs")])
        out.append(_r(code == runner.Outcome.HALTED,
                      "exit 2 = unusable input",
                      "a missing decisions file still halts",
                      f"a missing decisions file now exits {code}, not 2"))

    if not full:
        out.append(Result(WARN, "exit 0 and 3",
                          "not checked; --full runs the CLI twice against cached data"))
        return out

    ngara = REPO / "decisions" / "ngara.yaml"
    if not ngara.exists():
        out.append(Result(WARN, "exit 0 and 3", "decisions/ngara.yaml is gone"))
        return out

    with tempfile.TemporaryDirectory() as tmp:
        base = ["--country", "Tanzania", "--adm2", "Ngara", "--iso3", "TZA",
                "--decisions", str(ngara), "--out", str(Path(tmp) / "runs"),
                "--format", "bundle"]
        code = _exit(base, scope=False)
        out.append(_r(code == runner.Outcome.BELOW_FLOOR,
                      "exit 3 = below the review floor",
                      "a run under the floor still issues nothing",
                      f"a run under the floor now exits {code}, not 3; the "
                      "interface would present an unissued plan as issued"))
        code = _exit(base + ["--force-issue"], scope=False)
        out.append(_r(code == runner.Outcome.ISSUED,
                      "exit 0 = issued",
                      "a forced run still issues",
                      f"a forced run now exits {code}, not 0"))
    return out


def _exit(args: list[str], scope: bool = True) -> int:
    cmd = [sys.executable, "-m", "siting.cli"]
    if scope:
        cmd += ["--country", "Tanzania", "--adm2", "Ngara", "--iso3", "TZA"]
    return subprocess.run(cmd + args, cwd=str(REPO),
                          capture_output=True, text=True, timeout=600).returncode


def main(argv: list[str]) -> int:
    full = "--full" in argv
    results: list[Result] = []
    results += check_register()
    results += check_overrides()
    results += check_stages()
    results += check_flags()
    results += check_results()
    results += check_exit_codes(full)

    width = max(len(r.check) for r in results)
    for r in results:
        print(f"  {r.status:<7} {r.check:<{width}}  {r.detail}")

    broken = [r for r in results if r.status == BAD]
    warned = [r for r in results if r.status == WARN]
    print()
    print(f"{len(results)} checks: {len(results) - len(broken) - len(warned)} ok, "
          f"{len(warned)} warning, {len(broken)} broken")
    if broken:
        print("\nThe web layer's assumptions no longer hold. Fix web/, not siting/.")
    elif not full:
        print("Run with --full to also confirm the CLI's exit codes.")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
