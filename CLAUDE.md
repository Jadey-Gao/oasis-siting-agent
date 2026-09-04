# Siting agent

An agent that recommends where a district should place its next few facilities,
and produces an account of how it got there that a reviewing officer can check.

The recommendation is never the point. The account is. Anyone can produce a list
of coordinates; what a district can act on is a list whose derivation is
inspectable, whose assumptions are named, and whose disagreements with the
officer who reviewed it are recorded along with what they cost.

---

## The one thing to understand first

Certain judgements cannot be derived from data:

- **What distance counts as served.** This sets who the assessment says already
  has service, and therefore the size of the gap it reports.
- **Whose need the programme answers.** Reaching the most people and reaching
  the worst-served people are different objectives with different answers.
- **Whether a register of a given age is fit to direct capital spending.**
- **How much coverage a local veto may forgo before that is escalated.**

An earlier version of this system carried all four as constants in the source.
That moved the decision from the officer accountable for it to whoever wrote the
file. They now live in a register (`siting/decisions.py`), and **the analysis
refuses to run without them rather than assuming one**.

## Two modes

| | `--mode manual` (default) | `--mode auto` |
|---|---|---|
| Value-laden decisions | Stop until a person records each one | The agent takes them |
| Attribution | The person's name and reason | **The agent's name**, and its basis |
| The output says | "All N decisions were recorded by a person" | "N of M were taken by the agent and are attributed to it" |
| `budget` | Required | **Still required.** It comes from a capital programme; there is nothing to infer it from |

Automatic mode is not a lower standard, it is a different signature. The cover
of an automatic-mode bundle carries a marked paragraph naming how many
judgements were the machine's, and Ex00 lists them with the basis for each.

**Never describe an automatic-mode decision as the district's position.**

---

## Running one

```bash
# manual: prepare the decisions, then run
python -m siting.cli --country Uganda --adm2 Kiryandongo --iso3 UGA \
    --decisions decisions/kiryandongo.yaml \
    --overrides overrides/kiryandongo.yaml \
    --format both

# automatic: unattended, agent-attributed
python -m siting.cli --country Uganda --adm2 Kiryandongo --iso3 UGA \
    --mode auto --budget 10 --format bundle
```

Running with nothing recorded prints the outstanding decisions, the question
each answers, and why none of them has a default. That is the design working.

| Flag | |
|---|---|
| `--decisions` | The decisions file. Manual mode needs it |
| `--mode` | `manual` (default) or `auto` |
| `--overrides` | The reviewing officer's four verbs |
| `--format` | `bundle` \| `assessment` \| `both` |
| `--reviewer` | `rules` (default) or `llm` |
| `--resume` | A run id; completed artefact stages are skipped |
| `--force-issue` | Issue below the review floor, recorded as forced |
| `--benchmark` | Also solve exactly with spopt. Slow |

## Layout

```
skills/siting-run/SKILL.md        how to conduct an assessment. Read this first
skills/spatial-siting/SKILL.md    the analysis decision framework and guardrails
.claude/agents/                   data-scout, spatial-analyst, plan-reviewer
harness/hooks/                    pre/post tool use, stop
settings.json                     permissions and hook mounts
handbooks/*.yaml                  one per source: endpoint, semantics, cleaning
decisions/*.yaml                  what a person decided, and why
overrides/*.yaml                  the four verbs
siting/                           the tools. Read-only during a run
runs/<run-id>/                    everything one run produced
```

## Agents and their tools

Tool grants are the mechanism, not a suggestion.

| Agent | Tools | Why |
|---|---|---|
| `data-scout` | Read, Bash, Grep | Reports what the register holds. Cannot write, so cannot decide |
| `spatial-analyst` | Read, Bash, Grep | Prices each decision on real data. **Cannot write to `decisions/`**: preparing a choice and making it are different acts |
| `plan-reviewer` | **Read** | Cannot amend the plan it is judging, cannot re-run the analysis, does not see the analyst's reasoning |
| `map-reviewer` | **Read** | The only check that looks at a picture. Sees the images and the assertions they must satisfy, and cannot re-render or restyle |

## What the harness refuses

`settings.json` denies whole classes; `harness/hooks/pre_tool_use.sh` covers what
a pattern cannot.

- **`siting/` and `handbooks/` are read-only during a run.** If a tool is wrong,
  that is a finding to report, not a file to change. Silently adjusting the
  instrument to fit the answer is the failure this structure exists to prevent.
- **No synthetic fallback.** A recommendation built on invented data is worse
  than no recommendation.
- **A source without its credential is excluded, and the exclusion is printed.**
  Never left to be inferred from a missing figure.
- **A run does not publish.** Delivery is a separate, deliberate act.

## Guarantees that hold whatever the agent decides

- Distance is computed in a projected CRS, and the departure from great-circle is
  measured and reported rather than assumed negligible.
- Coverage is the union over facilities. The evaluator recomputes it
  independently and reports what a per-facility sum would have claimed.
- Aggregation sensitivity, boundary exposure and equity are tested every run.
- Reach is measured on the basis the district chose: straight-line distance, or
  walking time over a published friction surface.
- The area of interest is the administrative boundary where the boundary source
  and the register can be shown to describe the same place, and an envelope
  around the records where they cannot. Which one was used is always stated.
- The rendered figures are checked against the account, or recorded as
  unreviewed. Unreviewed is not a pass.
- Every figure in either document resolves to a recorded retrieval.
- Both documents compile from one `results.json`, so they cannot disagree.
- A reviewing officer's override is never blocked, always priced.

## Reading a result

`runs/<run-id>/RUN_RECORD.md` carries the whole account in plain text: who
decided what, the retrievals, the anomalies, the overrides and their cost, the
gate decisions, the review scores. Open it before the PDF.

## Lineage

The harness follows NORA (Zhou, Huang, Ning, Wu, Li and Zhang, 2026,
arXiv:2605.02092), which formalises lifecycle hooks, safety gates,
generator-evaluator separation, state persistence and human checkpoints for
autonomous research agents, and whose principle is that **skills encode intent
and the harness encodes guarantees**.

Two things are different here, both following from the fact that this produces a
decision rather than a manuscript. The review dimensions are data adequacy,
method fitness, spatial rigour, accountability and actionability, not novelty and
clarity. And where NORA pauses for approval, this refuses to assume, and prices
the disagreement when it comes.
