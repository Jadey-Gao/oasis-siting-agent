# Siting run record: Mayuge, Uganda (water)

> Rural water point siting. Budget 10. Generated 2026-09-04T15:59:05Z.
> Provenance hash `ac15a4eae9de9c52`, manifest `70831fd2c0b51651`.

## Command

```sh
python -m siting.cli --country Uganda --adm2 Mayuge --iso3 UGA --domain water --mode manual --out sessions/mayuge-20260904T152614Z-6127d5/runs --format both --reviewer rules --decisions sessions/mayuge-20260904T152614Z-6127d5/decisions.yaml
```

The `--out` and `--decisions` paths above have been shortened to be relative to the repository root. This run was produced under sessions/mayuge-20260904T152614Z-6127d5/ and the directory was copied to sample-runs/mayuge-uganda/ for publication. Only the machine-specific path prefix was removed; the manifest hash in manifest.json was recomputed over the shortened command, and nothing else in this record was altered.

## Decisions

All 8 decisions in this run were recorded by a person.

| Decision | Value | Recorded by | Reason |
|---|---|---|---|
| `service_radius_m` | 1000.0 | Jadey | 1000 m  Mayuge already reports against, |
| `coverage_basis` | walking_time | Jadey | walking time : Mayuge walk around water rather than across it |
| `objective` | max_coverage | Jadey | 198,832 unserved is too many to spend a small programme on the margins |
| `budget` | 10 | Jadey | we have varied budget, we want to find an ideal number |
| `data_currency_accepted` | yes | Jadey | conditional, some will have been repaired and some of the 354 will have failed |
| `coverage_tolerance` | 0.05 | Jadey | 198,832 unserved is too many to spend a small programme on the margins |
| `review_floor` | 6.5 | Jadey | don't be too strict |
| `equity_accepted` | unresolved | not settled at run time | The measured distribution was reported to the operator and no position was recorded, so the assessment states it as unresolved rather than accepted. |

## Result

| | |
|---|---|
| Population in the area of interest | 570,781 |
| Covered before | 371,949 (65.2%) |
| Covered after | 432,157 (75.7%) |
| Newly covered | 60,208 |
| Sites | 10 |

## Exhibits

| Ex | Content | Supports |
|---|---|---|
| Ex00 | Decisions taken, and by whom | That the judgements this assessment rests on are attributable |
| Ex01 | Rural water point siting register for Mayuge | The installed stock and its recorded condition |
| Ex02 | Population surface | The denominator for every coverage figure in this bundle |
| Ex03 | Coverage before any intervention | The size of the gap the recommendation is trying to close |
| Ex04 | Candidate sites considered | That the recommendation was selected from a stated, reproducible set |
| Ex05 | Selection method and its guarantee | That the ranking is reproducible and its optimality is bounded |
| Ex06 | Recommended sites | The recommendation itself, in coordinates |
| Ex07 | Planner overrides and what they cost | That the recommendation was reviewed by a person, and where it was overruled |
| Ex07b | Safety gate decisions | That access, transfer and fallback decisions were taken deliberately |
| Ex08 | Independent checks | That the plan was checked by a process that did not produce it |
| Ex08b | Scoring review | That the account was scored against a stated floor before issue |
| Ex09 | Sensitivity of the recommendation | How far the recommendation depends on assumptions the planner can change |
| Ex10 | Anomalies in the source data, and how they were handled | That the source registers were read rather than assumed |
| Ex11 | Reproduction record | That this bundle can be regenerated from the recorded queries |

## Retrievals

| Source | Retrieved | Read from | Raw | Clean | Licence |
|---|---|---|---|---|---|
| WPdx+ | 2026-09-04 | local cache | 1,378 | 1,251 | CC BY 4.0 |
| GADM | 2026-09-03 | local cache | 58 | 58 | GADM licence, free for academic and non-commercial use |
| WorldPop | 2026-09-03 | local cache | 15,912 | 8,992 | CC BY 4.0 |
| MAP friction surface | 2026-09-04 | local cache | 5,670 | 5,670 | CC BY 4.0 |

## Anomalies recorded

1. **semantics / WPdx+** status_clean is not two-valued in this district. Values present: Non-Functional (891); Functional, needs repair (354); Functional, not in use (6).
   - Handling: Serving status is read from the status_semantics block in handbooks/wpdx.yaml, which treats Functional and Functional, needs repair as serving and everything else as not serving.
   - Bearing: An equality test against the string Functional would score every point in this district as not serving and inflate the gap.
2. **duplication / WPdx+** 127 of 1,378 returned records were removed: flagged duplicate by WPdx.
   - Handling: Removed before any coverage computation, per the cleaning rules declared in the handbook.
   - Bearing: Coverage computed on the raw response would count these records twice or count superseded observations.
3. **method / MAP friction surface** Over this area the walking friction surface implies 21 minutes per kilometre at the median and 60 at the 90th percentile, so the chosen 1000 m corresponds to about 21 minutes of walking.
   - Handling: Coverage is tested as walking time rather than straight-line distance. Reach is computed per candidate from the local friction value rather than by a least-cost accumulation from every candidate, which would be exact but is not tractable at this candidate count.
   - Bearing: Where terrain changes sharply within one facility's reach, the local-friction approximation applies the conditions at the site in every direction, and is conservative rather than optimistic. The surface is modelled at about one kilometre and does not represent local footpaths.

## Scoring review

Weighted **8.12** against a floor of 6.50: **issue**. Reviewer: deterministic rules over results.json.

| Dimension | Score | Floor | Weight | Outcome |
|---|---|---|---|---|
| data adequacy | 8.5 | 5.0 | 0.25 | met |
| method fitness | 7.5 | 5.0 | 0.20 | met |
| spatial rigour | 7.0 | 6.0 | 0.25 | met |
| accountability | 9.0 | 6.0 | 0.20 | met |
| actionability | 9.5 | 4.0 | 0.10 | met |

## Gate decisions

| Gate | Outcome | Decided by | Reason |
|---|---|---|---|
| source authorisation | allowed | harness | Water Point Data Exchange Plus (WPdx+) is a public endpoint; no authorisation required |
| source authorisation | allowed | harness | WorldPop gridded population is a public endpoint; no authorisation required |
| guardrail breach | allowed | harness | Overrides forgo 0 people, 0.0% of the achievable coverage, within the 5% tolerance set for this run. |

## Independent checks

| Check | Result | Finding |
|---|---|---|
| coverage arithmetic | PASS | union recount matches at 432,157; across the 10 new sites a per-facility sum would report 68,916 against a true union of 68,602, an overcount of 313 |
| geometry | PASS | all 10 sites lie inside the area of interest |
| budget | PASS | 10 sites within the budget of 10 |
| coordinate reference system | PASS | EPSG:32636 (WGS 84 / UTM zone 36N); over 400 sampled pairs the projected distance departs from great-circle by 0.317% at the median and 0.595% at most, or about 6 m on a 1000 m service radius |
| data currency | FLAG | median source record is 1.9 years old. The age was put to the accountable officer and accepted: conditional, some will have been repaired and some of the 354 will have failed |
| aggregation sensitivity | PASS | reweighting the demand surface within cells moves the coverage claim from 75.7% to 75.7%, a shift of 0.02% |
| boundary effect | FLAG | 2 of 10 sites lie within one service radius of the district boundary; the nearest is 625 m from it. Population and facilities across the boundary are not represented, so coverage at these sites may be misstated |
| equity | FLAG | 59% of newly covered people live in the densest quartile of cells, against 42% district-wide; the plan favours dense settlements over remote ones |
| provenance | PASS | 4 sources recorded, all figures traceable |
| cartographic consistency | FLAG | the rendered figures were not reviewed against the account; run the map-reviewer agent over figures.json and pass its verdict with --figure-review |
