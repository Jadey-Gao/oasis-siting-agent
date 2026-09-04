# Open siting agent

An autonomous agent that retrieves open geospatial data, decides where a local
government should place the next few facilities, and produces a policy brief in
which every figure is traceable to a recorded retrieval and every point at which
a human overruled the machine is written down along with what that decision cost
in population coverage.

Built for the OASIS 2026 student challenge at ACM SIGSPATIAL.

## Two ways to run it

Same agent either way — same skill, same four subagents, same refusal to assume
a value-laden decision. Only where the conversation happens changes.

| | In Claude Code | Web interview |
|---|---|---|
| For | someone already working in this repo | an officer who doesn't use Claude Code |
| Writes to | `runs/`, `decisions/*.yaml` | `sessions/<id>/runs/`, `sessions/<id>/decisions.yaml` |

Both sit on top of `python -m siting.cli`, the plain tool with no conversation —
see `CLAUDE.md` to run that directly once a decisions file exists.

### In Claude Code

```bash
git clone https://github.com/Jadey-Gao/oasis-siting-agent.git
cd oasis-siting-agent
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
claude
```

Then, inside Claude Code:

```
/siting Uganda Mayuge water
```

- Manual mode by default — the agent puts every value-laden decision to you
  with the evidence and priced options. `/siting Uganda Mayuge water, mode auto`
  lets it decide instead, and attributes each one to itself.
- To resume a run, tell `/siting` the run id; it reads `handoff.json` and
  continues from the stage it stopped at.
- `settings.json` and `harness/hooks/` enforce the rest: `siting/` and
  `handbooks/` are read-only mid-run, only `decisions/` and `runs/` are writable.

### Web interview

Not hosted anywhere — run it on your own machine, against your own key.

```bash
git clone https://github.com/Jadey-Gao/oasis-siting-agent.git
cd oasis-siting-agent
pip install huggingface_hub
python scripts/fetch_cache.py --country UGA   # ~120 MB, optional
export ANTHROPIC_API_KEY=sk-ant-...
docker compose up
```

Open **http://localhost:7860**. Run it again after any dependency change with
`docker compose up --build` — compose reuses the old image otherwise.

Without Docker (Python 3.12, Node 20 for the SDK engine):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt      # do this before opening the page — see below
npm install -g @anthropic-ai/claude-code
uvicorn web.app:app --port 7860
```


### Where a run lands

```
<run-id>/
  evidence-bundle.pdf   the bundle: cover statement, exhibit index, Ex01-Ex11
  assessment.pdf        the chaptered assessment report
  RUN_RECORD.md         the same record in plain text
  results.json          the single input both documents compile from
  manifest.json         replay this to regenerate the bundle
  figure_review.json    the map reviewer's verdict, where it was run
  map_situation.png     Ex03
  map_plan.png          Ex06
```

Neither `runs/` nor `sessions/` is in the repository — a session holds the
verbatim transcript of whoever was interviewed. `sample-runs/mayuge-uganda/` is
one run copied out deliberately, described below.

## The worked example

`sample-runs/mayuge-uganda/` is one complete run as it was produced: **Mayuge
district, Uganda, ten water points, manual mode.** It is the only compiled
bundle anyone can read without running the agent themselves.

| | |
|---|---|
| Register | 1,378 WPdx+ records, 127 dropped as flagged duplicates, 1,251 kept — 354 serving, 897 not |
| Population in the area of interest | 570,781 |
| Covered before | 371,949 (65.2%), reach measured as walking time: about 21 minutes per kilometre at the median friction over this terrain |
| Covered after ten sites | 432,157 (75.7%), 60,208 newly covered |
| Distance | EPSG:32636, departing from great-circle by 0.32% at the median — about 6 m on a 1 km radius |
| Review | issue, weighted 8.12 against the floor of 6.5, with every dimension floor met |
| Flags raised and printed | data currency, boundary exposure, equity, and the figures unreviewed at issue |

Open `RUN_RECORD.md` first: it carries the whole account in plain text. The two
PDFs are the same run as an evidence bundle and as a chaptered assessment,
compiled from the one `results.json` so they cannot disagree.

**Who decided.** Seven of the eight decisions were recorded through the web
interview by the author of this system, acting as the officer; the interview
copies a reason verbatim rather than writing one. The eighth — whether the
measured equity distribution is accepted — was not settled before the run, and
the assessment carries it as unresolved rather than as agreed. Nobody at Mayuge
district made any of them. Read the names and reasons in that bundle as a demonstration of the
format, not as a record of a district's position — a decision record naming
someone who did not decide is the exact failure this design exists to prevent,
and an example is not exempt from it. `decisions/mayuge.yaml` is the register
that interview produced.

**What it does not hide.** Four of the ten independent checks came back as flags,
and all four are printed in Ex08 rather than resolved out of it. Three are
properties of this district and this plan: the median record is 1.9 years old,
two of the ten sites sit within one service radius of the district boundary, and
59% of the newly covered live in the densest quartile of cells against 42%
district-wide, so this plan favours dense settlements over remote ones. The
fourth is the figures. `figure_review.json` holds the map reviewer's verdict on
them — `revise` on both maps, for a legend that gives the population raster no
entry, and for service circles drawn as 1 km geometry when the coverage rule is
a walking time over terrain. It was recorded after the bundle had been compiled,
so the bundle's own cartographic check reads **unreviewed**, and unreviewed is
not a pass. It is left that way rather than tidied: an example that only shows
the clean path is not evidence of anything.

**One thing was edited after the run.** The recorded command carried the absolute
path of the machine it ran on. That prefix was removed so the paths read relative
to the repository root, the manifest hash was recomputed over the shortened
command, and the edit is disclosed in Ex11 and in `RUN_RECORD.md` beside the
command itself. Nothing else in the bundle was touched, and the provenance hash
`ac15a4eae9de9c52` is unchanged because it covers the retrievals, not the
invocation.

## The deliverable is an evidence bundle, not a report

The output is not a designed document. It is a short cover statement that argues
a recommendation, and behind it eleven numbered exhibits. No figure in the cover
statement is asserted without an exhibit number after it, and every exhibit opens
with the same three fields: what it supports, what was captured and from where,
and by what method.

| Ex | Content |
|---|---|
| 01 | The register: the query as executed, record counts, drops by rule |
| 02 | The population surface: bounding box, aggregation, totals |
| 03 | Coverage before any intervention |
| 04 | The candidate set the recommendation was chosen from |
| 05 | Selection method, its guarantee, and the marginal coverage curve |
| 06 | The recommendation itself, in coordinates |
| 07 | Planner overrides, the reason given for each, and what each cost |
| 08 | Independent checks, including any that returned a flag |
| 09 | Sensitivity to the assumptions a planner can change |
| 10 | **Anomalies found in the source data, and how they were handled** |
| 11 | Reproduction record: hashes, command, sources and licences |

Ex10 is the one that matters most. It records properties of the published data
that would have changed a coverage figure had they been read differently, with
what was observed, what the agent did, and what a reader must keep in mind. On
the Mayuge run it carries three. `status_clean` is not two-valued in that
district — 891 `Non-Functional`, 354 `Functional, needs repair`, 6 `Functional,
not in use` — so the equality test against `Functional` that a reader might
assume would have scored every point in Mayuge as broken and inflated the gap by
354 points. 127 of the 1,378 rows WPdx+ returned were flagged duplicates and
were dropped before any coverage number was computed. And walking-time reach is
taken from the local friction value at each candidate rather than by a least-cost
traverse from every one of them, which is conservative rather than optimistic,
and is stated as a property of the method rather than buried in it.

## What it does

Four public-health siting questions reduce to one spatial problem: a weighted
demand surface, a set of candidate locations, a rule deciding which demand a
candidate serves, and a budget. Adding a domain is an adapter and a config entry,
not a second codebase.

| Domain | Demand weight | Candidates | Coverage rule | Status |
|---|---|---|---|---|
| Water points | population beyond walking distance of a serving point | non-serving points, greenfield lattice | 1 km, projected | **implemented** |
| Health access | population, optionally women of reproductive age | OSM facilities, lattice | travel time over a friction surface | **not written**; handbook only |
| Disease burden | population x parasite prevalence | as above | travel time | **not written**; handbook only |
| Air monitoring | population x exposure x monitoring gap | lattice | representativeness radius | **not written**; needs an OpenAQ key |

Only the water adapter exists. The other three have source handbooks and a
compiler interface waiting for them, and nothing more. The L2 contract is what
makes adding one a config and an adapter rather than a second codebase, but that
claim is untested until the second one is written.

## The five harness mechanisms

NORA formalises five mechanisms that make an autonomous research agent reliable.
All five are implemented here, adapted from a research pipeline to a decision
pipeline, which changes what two of them are for.

| Mechanism | Where | What it does here |
|---|---|---|
| Lifecycle hooks | `harness.Hooks` | Validates a stage before it runs, persists what it produced after, and writes `hook_log.json` and `MEMORY.md` on exit, so an interrupted run leaves an account of itself |
| Safety gates | `harness.Gate` | Refuses or escalates before an action, and records which. A key-gated source without its key is excluded and **the exclusion is stated**, not left to be inferred from a missing figure. Synthetic fallback is refused outright without a written approval |
| Generator-evaluator separation | `siting/review.py` | A reviewer that receives `results.json` and nothing else, scoring five dimensions each with a floor. It cannot see the solver, the code path, or the override reasons |
| State persistence | `harness.Handoff` | `handoff.json` records stage position and artefacts; `--resume <run-id>` continues from it |
| Human-in-the-loop | `siting/overrides.py` | Nine named checkpoints, and the four verbs. **This is where the two pipelines diverge**: NORA pauses for approval, this prices the decision and carries the cost into the output |

### The review dimensions are not NORA's

NORA scores manuscripts on novelty, rigour, literature coverage, clarity and
impact. Those are the wrong questions for a decision. What an officer needs to
know before acting is scored instead:

| Dimension | Weight | Floor | Asks |
|---|---|---|---|
| data adequacy | 0.25 | 5.0 | Authoritative, current enough, sufficient coverage? |
| method fitness | 0.20 | 5.0 | Does the location model answer the question asked, with a stated bound? |
| spatial rigour | 0.25 | 6.0 | Projected CRS, union coverage, MAUP, boundary, equity: present and passing? |
| accountability | 0.20 | 6.0 | Every figure traceable, every override priced, every anomaly disclosed? |
| actionability | 0.10 | 4.0 | Coordinates, a ranking whose order matters, honest limitations? |

Weighted average must clear 6.5 **and** every floor must be met. Otherwise
nothing is issued. `--force-issue` overrides that, and the output says it was
forced.

The reviewer is deterministic by default, because a floor that moves between
runs is not a floor. `--reviewer llm` scores with a model in an isolated context
when `ANTHROPIC_API_KEY` is set, and falls back to the deterministic reviewer
rather than failing the run, naming which was used either way.

### What `--resume` actually skips

A resume that lies is worse than no resume. The first five stages hold their
results in memory, so a resumed run re-executes them: retrieval is served from
the local cache, so no request is re-issued and the provenance hash is
unchanged, and the rest is deterministic recomputation. Only `score`, `render`
and `issue` are genuinely skipped, and the log says which of the two happened
for every stage.

## Skills and harness

The split follows NORA (Zhou, Huang, Ning, Wu, Li and Zhang, 2026,
arXiv:2605.02092): **skills encode intent, the harness encodes guarantees.**

`skills/spatial-siting.md` is the decision framework: which location model
answers which question, what must be settled before a demand surface exists,
which diagnostics are not optional, and eight guardrails that invalidate a
result if violated. It states what to consider, not what to execute.

The harness is everything that makes those guarantees hold whatever the agent
decides to do: declarative cleaning rules that report their own drop counts, a
provenance record per retrieval, an evaluator in a separate process that can
reject the plan, and a manifest whose replay reproduces the run.

The guardrails are enforced, not advisory. Distance is computed in a projected
CRS and the departure from great-circle is measured and reported; coverage is a
union and the evaluator states what a per-facility sum would have claimed;
aggregation sensitivity and boundary exposure are tested on every run.

## Layers

| | | Owner |
|---|---|---|
| L0 | `handbooks/*.yaml` — one knowledge file per source: endpoint, field semantics, cleaning rules | machine |
| L1 | `siting/sources/` — retrieve, clean, emit a provenance record per pull | machine |
| L2 | `siting/compile.py` — compile a domain into the canonical four-slot instance | machine |
| L3 | `siting/solve.py` — greedy submodular maximisation with a coverage guardrail | machine |
| L4 | `siting/overrides.py` — four verbs, each priced in people | **planner** |
| L5 | `siting/evaluate.py` — independent checks that can reject the plan | machine |
| L6 | `siting/report/` — figures, then a Typst brief compiled from `results.json` | machine |
| L7 | `manifest.json` — replay reproduces the brief | machine |

## The four verbs

Overrides live in a YAML file under version control, not in a UI, so a planner's
decisions are a reviewable artefact rather than an ephemeral click.

```yaml
- verb: VETO           # site, reason, veto_radius_m
- verb: PIN            # site, reason
- verb: REWEIGHT       # equity_weight, reason
- verb: RESCOPE        # radius_m or budget, reason
```

A reason is mandatory; an override without one is refused. `VETO` excludes an
area rather than a lattice point, because a planner saying "not there" means a
place, and vetoing a single point is nearly free when the next candidate sits
750 m away.

Every override triggers a re-solve and produces a diff: what it cost in people,
which sites changed. The guardrail prices the whole set against the
unconstrained plan. It warns; it never blocks. The planner has the final word,
and the system's job is to make sure the cost of that word is written down.

## Design decisions worth defending

- **Greedy is the primary solver, not the exact one.** `spopt`'s MCLP with CBC
  takes over two minutes on a 300 x 60 toy instance; the override loop re-solves
  after every decision. `solve.benchmark()` runs the exact model on small
  instances to quantify the gap, and is not in the loop.
- **Coverage is a union, never a sum.** Summing population per facility double
  counts every settlement reachable from two of them. L5 recomputes the union
  independently and reports what the naive sum would have claimed.
- **The evaluator is a separate process from the generator** and never reads the
  override reasons. It checks the plan against the data, not against the story
  told about the plan.
- **The language model only translates.** Free text becomes one of four verbs and
  nothing else. It never proposes an allocation, because people reweight their own
  judgement once a machine recommendation is in front of them.
- **Distance is computed in a projected CRS.** UTM for the area of interest,
  chosen and justified per run, with the departure from great-circle measured
  and reported rather than assumed negligible. Computing distance from decimal
  degrees is the first item on NORA's list of common spatial mistakes, and an
  earlier version of this system committed it.
- **Age is reported, not filtered.** Uganda district holdings range from a mean of
  6 to over 16 years old. Dropping stale points would delete whole districts, so
  the median age of surviving records goes into the brief's Limitations section.

## Traps the handbooks encode

- WPdx+ ships duplicate rows and superseded observations in the same dataset.
  `is_duplicate` and `is_latest` must both be applied before any coverage number
  is believable.
- `status_clean` is not two-valued. Uganda returns "Functional, needs repair",
  "Functional, not in use" and "Non-Functional"; testing equality against
  "Functional" scores every point in the district as broken.
- WorldPop's server refuses HTTP range requests, so `/vsicurl/` windowed reads
  fail and the national raster is cached whole (107 MB for Uganda).
- The dataset id is `eqje-vguj` (WPdx+), not `jfkt-jmqa` (the older Basic set).

## Install

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

That is the whole of it. `requirements.txt` names every package `siting/` and
`web/` import, including the ones that would otherwise arrive by luck through
geopandas — a dependency inherited rather than declared is a dependency until
the package carrying it drops it.

GOSTnetsraster is **not** required, despite `requirements.txt` naming it as an
install-separately: `sources/friction.py` reaches for `skimage.graph.MCP_Geometric`
directly and nothing imports GOSTnets. Installing it is harmless and unnecessary.

Typst packages download on first compile. QGIS is optional: `maps.py` renders
with matplotlib by default and `render_with_qgis()` is the cartographic upgrade,
run as a subprocess so QGIS's GPL-2.0 licence stays behind a process boundary.

## Licence

MIT. Every dependency is MIT, MIT-0, BSD-3, Apache-2.0, or — matplotlib alone —
the BSD-style matplotlib licence.
