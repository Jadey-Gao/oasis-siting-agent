# Siting run record: Masindi, Uganda (water)

> Rural water point siting. Budget 10. Generated 2026-08-27T03:06:57Z.
> Provenance hash `0e193d1063ade2ac`, manifest `b8a961281acc1dc7`.

## Command

```sh
python -m siting.cli --country Uganda --adm2 Masindi --iso3 UGA --decisions decisions/masindi.yaml --format bundle
```

## Decisions

All 7 decisions in this run were recorded by a person.

| Decision | Value | Recorded by | Reason |
|---|---|---|---|
| `service_radius_m` | 1000 | District Water Officer, Masindi | One kilometre corresponds to the 30 minute round trip the JMP uses as a basic drinking water service threshold, and to the figure the district already reports against. Adopted for continuity with existing reporting rather than because the terrain has been assessed. |
| `coverage_basis` | walking_time | District Water Officer | Reach is measured as walking time over the modelled terrain rather than as straight-line distance, because the straight-line measure counts households as served across the Kafu and the seasonal swamps that they cannot in fact cross on foot. The friction surface is coarse and does not know local footpaths, and that limitation is accepted in preference to ignoring terrain altogether. |
| `objective` | worst_case | District Water Officer, Masindi | The 2026 district development plan prioritises the refugee settlement sub-counties and the dispersed eastern parishes, which are the households currently furthest from any serving point. Reducing the worst-served distance is therefore the objective, accepting a lower total covered than maximum coverage would reach. The analyst reported the trade as roughly 12,000 people of coverage against a fall in the worst-served distance from 31.5 km to 8.1 km. |
| `budget` | 10 | District Water Officer, Masindi | Ten boreholes are funded under the FY2026 rural water capital allocation. |
| `data_currency_accepted` | yes, with the age stated in the report | District Water Officer, Masindi | The register's median record is 4.4 years old, ranging from 3.5 to 16.2 years. Accepted as the best available basis for a siting decision that cannot wait for a resurvey, on the condition that the assessment states the age prominently and describes its findings as the surveyed state of the network rather than its present state. |
| `coverage_tolerance` | 0.05 | District Water Officer, Masindi | Local vetoes forgoing more than five per cent of achievable coverage are to be reported to the District Water and Sanitation Coordination Committee rather than settled at officer level. |
| `equity_accepted` | yes | District Water Officer, Masindi | With the worst_case objective the plan is directed at remote households by design, so the distributional concern that applies to coverage maximisation does not arise in the same form here. The measured distribution is accepted. |

## Result

| | |
|---|---|
| Population in the area of interest | 816,304 |
| Covered before | 264,334 (32.4%) |
| Covered after | 299,034 (36.6%) |
| Newly covered | 34,700 |
| Sites | 10 |

## Exhibits

| Ex | Content | Supports |
|---|---|---|
| Ex00 | Decisions taken, and by whom | That the judgements this assessment rests on are attributable |
| Ex01 | Rural water point siting register for Masindi | The installed stock and its recorded condition |
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

| Source | Retrieved | Raw | Clean | Licence |
|---|---|---|---|---|
| WPdx+ | 2026-08-27 | 2,189 | 2,178 | CC BY 4.0 |
| GADM | 2026-08-27 | 58 | 58 | GADM licence, free for academic and non-commercial use |
| WorldPop | 2026-08-27 | 62,750 | 60,638 | CC BY 4.0 |
| MAP friction surface | 2026-08-27 | 19,044 | 19,044 | CC BY 4.0 |

## Anomalies recorded

1. **semantics / WPdx+** status_clean is not two-valued in this district. Values present: Functional (1,174); Functional, needs repair (578); Non-Functional (241); Abandoned/Decommissioned (170); Functional, not in use (15).
   - Handling: Serving status is read from the status_semantics block in handbooks/wpdx.yaml, which treats Functional and Functional, needs repair as serving and everything else as not serving.
   - Bearing: An equality test against the string Functional would score every point in this district as not serving and inflate the gap.
2. **currency / WPdx+** The median record in this district is 4.5 years old (oldest 16.3 years, newest 4.4 years).
   - Handling: Age is reported, not filtered. Uganda district holdings range from a mean of six to over sixteen years, so a staleness filter would delete whole districts rather than clean them.
   - Bearing: Coverage figures describe the surveyed state of the network, not necessarily its state today.
3. **method / MAP friction surface** Over this area the walking friction surface implies 14 minutes per kilometre at the median and 42 at the 90th percentile, so the chosen 1000 m corresponds to about 14 minutes of walking.
   - Handling: Coverage is tested as walking time rather than straight-line distance. Reach is computed per candidate from the local friction value rather than by a least-cost accumulation from every candidate, which would be exact but is not tractable at this candidate count.
   - Bearing: Where terrain changes sharply within one facility's reach, the local-friction approximation applies the conditions at the site in every direction, and is conservative rather than optimistic. The surface is modelled at about one kilometre and does not represent local footpaths.

## Scoring review

Weighted **7.67** against a floor of 6.50: **issue**. Reviewer: deterministic rules over results.json.

| Dimension | Score | Floor | Weight | Outcome |
|---|---|---|---|---|
| data adequacy | 7.5 | 5.0 | 0.25 | met |
| method fitness | 6.5 | 5.0 | 0.20 | met |
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
| coverage arithmetic | PASS | union recount matches at 299,034; across the 10 new sites a per-facility sum would report 34,700 against a true union of 34,700, an overcount of 0 |
| geometry | PASS | all 10 sites lie inside the area of interest |
| budget | PASS | 10 sites within the budget of 10 |
| coordinate reference system | PASS | EPSG:32636 (WGS 84 / UTM zone 36N); over 400 sampled pairs the projected distance departs from great-circle by 0.290% at the median and 0.583% at most, or about 6 m on a 1000 m service radius |
| data currency | PASS | median source record is 4.5 years old |
| aggregation sensitivity | PASS | reweighting the demand surface within cells moves the coverage claim from 36.6% to 36.7%, a shift of 0.03% |
| boundary effect | FLAG | 4 of 10 sites lie within one service radius of the district boundary; the nearest is 104 m from it. Population and facilities across the boundary are not represented, so coverage at these sites may be misstated |
| equity | FLAG | 100% of newly covered people live in the densest quartile of cells, against 68% district-wide; the plan favours dense settlements over remote ones |
| provenance | PASS | 4 sources recorded, all figures traceable |
| cartographic consistency | FLAG | the rendered figures were not reviewed against the account; run the map-reviewer agent over figures.json and pass its verdict with --figure-review |
