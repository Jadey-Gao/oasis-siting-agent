# Open siting agent

An autonomous agent that retrieves open geospatial data, decides where a local
government should place the next few facilities, and produces a policy brief in
which every figure is traceable to a recorded retrieval and every point at which
a human overruled the machine is written down along with what that decision cost
in population coverage.

Built for the OASIS 2026 student challenge at ACM SIGSPATIAL.

```
python -m siting.cli --country Uganda --adm2 Kiryandongo --iso3 UGA \
    --domain water --budget 10 --overrides overrides/kiryandongo.yaml
```

## Running the web interview

The interview is a conversation with the agent that ends in a recorded decisions
file and a run. It is not hosted anywhere: you run it on your own machine
against your own Anthropic key, because a hosted instance would mean strangers
spending somebody else's credit on an agent that holds a shell.

```bash
git clone https://github.com/Jadey-Gao/oasis-siting-agent.git
cd oasis-siting-agent

pip install huggingface_hub
python scripts/fetch_cache.py --country UGA   # ~120 MB, optional but wanted

export ANTHROPIC_API_KEY=sk-ant-...           # your own key
docker compose up
```

Then open **http://localhost:7860**.

The cache step is optional. Without it the first run goes to WorldPop for a
whole national population raster — 108 MB for Uganda, 462 MB for Tanzania —
which is slow but not wrong. `python scripts/fetch_cache.py` with no arguments
takes both countries; any other country is always retrieved live.

`sessions/`, `runs/` and `cache/` are mounted from the working directory, so
everything a run produces — the decisions file, `RUN_RECORD.md`, `results.json`,
the compiled PDFs — stays on your disk after the container stops.

Without Docker, the same thing runs directly, given Python 3.12 and Node 20
(the Agent SDK spawns the Claude Code CLI; set `SITING_WEB_ENGINE=legacy` to do
without both Node and the subagents):

```bash
pip install -r requirements.txt
npm install -g @anthropic-ai/claude-code
uvicorn web.app:app --port 7860
```

One run writes one directory under `runs/`:

```
runs/<run-id>/
  evidence-bundle.pdf   the bundle: cover statement, exhibit index, Ex01-Ex11
  assessment.pdf        the chaptered assessment report
  RUN_RECORD.md         the same record in plain text, for reading in the repo
  results.json          the single input the bundle is compiled from
  manifest.json         replay this to regenerate the bundle
  map_situation.png     Ex03
  map_plan.png          Ex06
  main.typ / lib.typ    the template as it stood for this run
```

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
the Kiryandongo run it caught a status value the agent's own handbook did not
declare, `Abandoned/Decommissioned`, 83 records, and treated them conservatively
as not serving rather than guessing. That value has since been added to the
handbook and the detector left in place for the next country.

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
pip install -r requirements.txt
pip install git+https://github.com/worldbank/GOSTnetsraster.git   # travel time domains
```

Typst packages download on first compile. QGIS is optional: `maps.py` renders
with matplotlib by default and `render_with_qgis()` is the cartographic upgrade,
run as a subprocess so QGIS's GPL-2.0 licence stays behind a process boundary.

## Licence

MIT. Every dependency is MIT, MIT-0, BSD-3 or Apache-2.0.
