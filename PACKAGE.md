# Package contents

An agent that recommends where a district should place its next facilities and
produces an account a reviewing officer can check.

Built for the OASIS 2026 student challenge at ACM SIGSPATIAL. MIT licensed.

---

## Start here

| Read | For |
|---|---|
| `CLAUDE.md` | What the system is and how to run it. Read this first |
| `sample-runs/masindi-uganda/RUN_RECORD.md` | A whole run in plain text: who decided what, every retrieval, every anomaly, every check |
| `sample-runs/masindi-uganda/evidence-bundle.pdf` | The same run as a numbered evidence package |
| `skills/siting-run/SKILL.md` | How an assessment is conducted, as a decision framework rather than a script |

## Layout

```
CLAUDE.md                      project dashboard, how to run
README.md                      longer form, design decisions, dependency licences
LICENSE                        MIT

skills/siting-run/SKILL.md     conducting an assessment: what to consider, where to stop
skills/spatial-siting/SKILL.md the analysis framework and eight guardrails

.claude/agents/                four subagents, separated by tool grant
  data-scout.md                  Read, Bash, Grep. Reports; cannot decide
  spatial-analyst.md             Read, Bash, Grep. Prices choices; cannot write decisions
  plan-reviewer.md               Read only. Judges the account; cannot amend it
  map-reviewer.md                Read only. The only check that looks at a picture
.claude/commands/siting.md     the entry point
settings.json                  permissions and hook mounts
harness/hooks/                 pre tool use, post tool use, stop

handbooks/*.yaml               one per source: endpoint, field semantics, cleaning rules
decisions/*.yaml               what a person decided, and why
overrides/*.yaml               the reviewing officer's four verbs

siting/                        the tools. Read-only during a run
  decisions.py                   the register of judgements that are not the code's
  harness.py                     hooks, gates, resumable state, checkpoints
  spatial.py                     projected CRS and the departure from great-circle
  compile.py                     the four-slot problem instance
  solve.py                       MCLP and p-centre
  overrides.py                   the four verbs, each priced
  evaluate.py                    ten independent checks
  review.py                      the scoring reviewer, five dimensions with floors
  sources/                       wpdx, worldpop, gadm, friction
  domains/water.py               the one implemented domain
  report/                        figures, the figure brief, two Typst documents

sample-runs/                   two complete runs, as produced
requirements.txt               dependencies, all MIT / BSD / Apache
```

## Running it

```bash
pip install -r requirements.txt

python -m siting.cli --country Uganda --adm2 Masindi --iso3 UGA \
    --decisions decisions/masindi.yaml --format both
```

Running with no decisions file prints the judgements that must be settled first,
the question each answers, and why none of them has a default. That is the design
working, not a failure.

`--mode auto` lets the agent take those judgements so a run can complete
unattended. Each one it takes is attributed to it in the output.

## A caution about the sample runs

The decision records in `decisions/` and the officer attributions inside both
sample runs are **illustrative and were written by the author of this system**.
No district officer made any of those decisions, and the reasons given are
plausible inventions rather than any district's actual position.

They are marked as such in the YAML, but the attribution is also carried into the
rendered PDFs and into `RUN_RECORD.md`, where it reads as a real person's
judgement. Read those documents as a demonstration of the format, not as a record
of anything a district decided.

This is worth stating plainly because attribution is the point of the system. A
decision record naming someone who did not decide is the exact failure the design
exists to prevent, and an example is not exempt from that.

## The sample runs

**`masindi-uganda`** is a complete manual-mode run: six decisions recorded by a
named officer, walking-time coverage over the Malaria Atlas friction surface, the
worst-case objective, an administrative boundary verified against the register,
ten checks, a scoring review, and a figure review that returned revise three
times before the maps agreed with the account.

**`western-rural-sierra-leone`** is the same pipeline in a second country with no
code changes, as evidence that the method transfers. Its decisions are marked as
a transferability test rather than any district's position, and its boundary check
correctly refused to use a boundary that did not correspond to the register.

Each contains `results.json`, the single file both documents compile from, so the
two cannot disagree; `manifest.json`, whose replay reproduces the run; and
`handoff.json`, which lets an interrupted run resume where it stopped.

## What is not here

- `cache/`, the retrieved data. Roughly 155 MB, and regenerated on first run.
- The other three domains. Health access, disease burden and air monitoring have
  source handbooks and a compiler interface waiting for them, and nothing more.
  Only the water adapter exists.
- Tests.
- A paper.

## Lineage

The harness follows NORA (Zhou, Huang, Ning, Wu, Li and Zhang, 2026,
arXiv:2605.02092): lifecycle hooks, safety gates, generator-evaluator separation,
state persistence and human checkpoints, on the principle that skills encode
intent and the harness encodes guarantees.

The figure reviewer takes its shape from the map reviewer in CartoAgent (Wang et
al., *IJGIS* 2025) but not its criteria. CartoAgent asks whether a map is
informative and visually appealing; this asks whether the picture and the account
agree, which is a question only answerable because `results.json` is available
alongside the image.
