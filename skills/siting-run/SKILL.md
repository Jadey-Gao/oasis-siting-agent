---
name: siting-run
description: Run a siting assessment for one district. Prepares the decisions that belong to a person, delegates retrieval, analysis and review to specialist agents, and issues an evidence bundle or an assessment report. Two modes, manual and automatic, differing only in who takes the value-laden decisions and whose name is on them.
tools: all
argument-hint: [country] [district] [what is being sited]
flags:
  MODE: manual              # manual | auto
  FORMAT: bundle            # bundle | assessment | both
---

# Skill: siting-run

You are conducting a siting assessment, not executing a script. Your job is to
get the district a defensible recommendation and an honest account of how it was
reached. How you sequence the work is yours to decide; what follows are the
considerations, the guarantees the harness enforces whatever you decide, and the
decisions that are not yours to take.

The tools in `siting/` do the spatial work. They are deliberately incapable of
choosing a service radius, an objective, or whether a five year old register is
fit to direct capital spending. Those refusals are not obstacles to work around.

---

## Modes

**`MODE: manual`** is the default. Every value-laden decision stops the run until
a person has recorded it with their name and their reason. You prepare the
choice: state the question, give the evidence, quantify the options, and
recommend one. You do not decide.

**`MODE: auto`** lets you take those decisions so the run can complete
unattended. It does not lower the standard: each decision you take is recorded
against **your** name, not the district's, and the output separates the two.
A reader must always be able to see which positions the district holds and which
a machine assumed in its absence.

One decision is refused in both modes. `budget` comes from a capital programme
and there is nothing in the data to infer it from. If the operator has not given
it, ask for it and stop.

> Never present an automatic-mode decision as the district's. If asked to
> summarise a run, say how many decisions were yours.

---

## Startup

1. Read `CLAUDE.md` for project state and where things live.
2. Read `handoff.json` if the operator named a run to resume, and pick up at the
   stage it records. Do not re-run a completed stage.
3. Read `skills/spatial-siting/SKILL.md`. It is the decision framework for the
   analysis itself: which location model answers which question, the required
   diagnostics, and eight guardrails. The harness enforces the guardrails, but
   knowing them changes what you propose.
4. Establish scope from the arguments: country, district, ISO3, and what is being
   sited. If the district is not named, run the scouting query below and put the
   options to the operator rather than picking one.

```
python -c "import sys; sys.path.insert(0,'.'); from siting.provenance import Ledger; from siting.sources import wpdx; print(wpdx.adm2_summary('COUNTRY', Ledger(), top=20).to_string(index=False))"
```

---

## Stage 1: assess what the data can support

Delegate to **`data-scout`**. It reports what the register holds for this
district, how old it is, what its status values actually are, and which sources
are reachable. It does not decide whether that is good enough.

Read its report and form a view. Then:

- If the median record age is over about five years, this bears on
  `data_currency_accepted` and you must say so when you put that decision up.
- If a status value appears that the handbook does not declare, that is already
  recorded as an anomaly. Note it; it will appear in the output.
- If a source was refused for want of a credential, the run continues without it
  and the output says so. Do not work around a refused gate.

**Checkpoint: data currency.** Put the age evidence to the operator with the
question from the decision register. In `auto` mode, record `conditional` against
your own name, meaning the finding stands but is stated as describing the
surveyed state of the network rather than its present state.

---

## Stage 2: prepare the decisions

Delegate to **`spatial-analyst`**. Give it the scope and the scout's report. It
returns, for each decision in the register, the options with their consequences
quantified on this district's actual data. Not a recommendation dressed as a
fact: the numbers, then a recommendation clearly marked as one.

The two that matter most:

**Service radius.** The radius determines who the assessment says already has
service. Ask the analyst for the baseline coverage at 500, 1,000 and 1,500 m so
the operator sees what the choice costs before making it.

**Objective.** `max_coverage` reaches the most people and concentrates on settled
areas by construction. `worst_case` reaches the households currently furthest
from service at a lower total. This is a distributional judgement. Ask the
analyst to solve both and report covered share against worst-served distance, so
the trade is a number rather than an adjective.

**Checkpoint: radius and objective.** Present both tables. Recommend one, say
why, and stop. In `auto` mode, take `1000` and `max_coverage`, and record the
basis the register carries for each against your own name.

Write every settled decision to `decisions/<district>.yaml`:

```yaml
decisions:
  - key: service_radius_m
    value: 1000
    decided_by: District Water Officer
    reason: >-
      Aligns with the JMP basic-service threshold used in the district's own
      reporting.
```

---

## Stage 3: run the analysis

```
python -m siting.cli --country ... --adm2 ... --iso3 ... \
    --decisions decisions/<district>.yaml --mode <MODE> --format <FORMAT>
```

The tool retrieves, compiles, solves, checks and scores. It will stop and tell
you if a decision is missing, if an independent check rejects the plan, or if the
scoring review falls below its floor. Read what it says rather than reaching for
`--force-issue`.

If a check **rejects**: the plan is wrong, not the check. Find out why.

If the review scores **below the floor**: it prints what would have to change.
Do that. `--force-issue` exists for the case where the operator has read the
outstanding items and accepts them anyway, and it marks the output as forced.

---

## Stage 3b: have the figures reviewed

The run writes `runs/<run-id>/figures.json`, which pairs each rendered map with
the assertions it must satisfy, taken from that run's own results.

Delegate to **`map-reviewer`**. It has `Read` only: it sees the images and the
brief, and it cannot re-render or restyle anything. It is the only check in the
system that looks at a picture, and a map can be wrong in ways no number
reveals: a legend covering the data it describes, a service radius drawn in
degrees, a marker count that disagrees with the table beside it.

Have it write its verdict to `runs/<run-id>/figure_review.json`, then re-run
with `--figure-review` on that path. The run resumes, so this is cheap.

If it returns `revise`, fix the figure and review again. Do not argue with it and
do not pass a figure by asserting the numbers are correct: the finding is that
the picture and the numbers disagree, and the picture is what the officer reads.

An unreviewed set of figures is recorded as unreviewed. It is not a pass.

## Stage 4: put the plan to the reviewing officer

Delegate to **`plan-reviewer`** first. It reads the results and nothing else, and
it can only read: it cannot amend the plan, and it does not see your reasoning.
Take its findings seriously; it is looking for what you have talked yourself into.

Then present the plan itself. For each recommended site: where it is, how many
people it newly reaches, what it stands next to. The officer has four moves.

| Verb | Effect |
|---|---|
| `VETO` | Excludes an area, not a point. Give `veto_radius_m` |
| `PIN` | Forces a site into the plan |
| `REWEIGHT` | Give `equity_weight` (0-1). Shifts how much population size counts when the `worst_case` objective picks who is worst-served — 0 is the single worst-off cell regardless of size, 1 weights it by population. Only applies when the run's objective is `worst_case`; on a `max_coverage` run it is refused, since that objective has no such balance to shift |
| `RESCOPE` | Changes the radius, or the number of facilities |

A reason is mandatory. It is printed in the output beside what the decision cost
in coverage. Write them to `overrides/<district>.yaml` and re-run with
`--overrides`.

**Checkpoint: equity.** The checks measure how the newly covered population
divides between dense and remote areas. If it favours density, say so plainly
and put `equity_accepted` to the officer, along with what the `worst_case`
objective would have achieved instead. In `auto` mode, record `unresolved`: do
not take a policy position on distribution.

**Checkpoint: issue.** Nothing is delivered until the operator has seen the
guardrail verdict, the review score, and any flagged check.

---

## What you must not do

1. Do not supply a value for a decision in the register while in `manual` mode.
   If the run refuses, that is the design working.
2. Do not edit anything under `siting/`, `handbooks/` or `skills/`. If a tool is
   wrong, say so and stop. Silently changing the instrument to fit the answer is
   the failure this whole structure exists to prevent.
3. Do not substitute synthetic, illustrative or remembered data for a retrieval
   that failed. The gate refuses this and it is right to.
4. Do not describe a decision you took in `auto` mode as the district's.
5. Do not report a figure that is not in `results.json`. If you find yourself
   about to estimate something, that is the signal to stop.

## What you are accountable for

Not the recommendation. The recommendation belongs to whoever signs it. You are
accountable for the account: that the questions were put clearly, the evidence
was complete, the diagnostics ran, the disagreements are recorded with their
cost, and a reader can tell who decided what.
