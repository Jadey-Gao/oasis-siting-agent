# Siting run record: Western Rural, Sierra Leone (water)

> Rural water point siting. Budget 10. Generated 2026-08-27T02:55:17Z.
> Provenance hash `03520a176445c3e9`, manifest `09e93c6639e2e611`.

## Command

```sh
python -m siting.cli --country Sierra Leone --adm2 Western Rural --iso3 SLE --decisions decisions/generic.yaml --format bundle
```

## Decisions

All 7 decisions in this run were recorded by a person.

| Decision | Value | Recorded by | Reason |
|---|---|---|---|
| `service_radius_m` | 1000 | transferability test | One kilometre, for comparability with the Uganda runs. Not a position held by any district in this country. |
| `coverage_basis` | straight_line | transferability test | Straight-line, to isolate whether retrieval and boundary matching transfer across countries without the friction surface as a second variable. |
| `objective` | max_coverage | transferability test | Maximum coverage, for comparability. Not a distributional position. |
| `budget` | 10 | transferability test | Ten, for comparability with the Uganda runs. |
| `data_currency_accepted` | test only, not fit for a real decision | transferability test | This run exists to check that the pipeline transfers, not to direct any spending. The register's age has not been assessed by anyone accountable. |
| `coverage_tolerance` | 0.05 | transferability test | Five per cent, for comparability. |
| `equity_accepted` | unresolved | transferability test | No policy position is taken in a transferability test. |

## Result

| | |
|---|---|
| Population in the area of interest | 1,520,809 |
| Covered before | 160,241 (10.5%) |
| Covered after | 736,548 (48.4%) |
| Newly covered | 576,307 |
| Sites | 10 |

## Exhibits

| Ex | Content | Supports |
|---|---|---|
| Ex00 | Decisions taken, and by whom | That the judgements this assessment rests on are attributable |
| Ex01 | Rural water point siting register for Western Rural | The installed stock and its recorded condition |
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
| WPdx+ | 2026-08-27 | 4,987 | 4,382 | CC BY 4.0 |
| GADM | 2026-08-27 | 4 | 4 | GADM licence, free for academic and non-commercial use |
| WorldPop | 2026-08-27 | 8,700 | 6,121 | CC BY 4.0 |

## Anomalies recorded

1. **coverage / GADM** No administrative boundary could be matched for 'Western Rural' in SLE: "no unit named 'Western Rural' in SLE at levels (1, 2, 3). level 1: not found among 4 units; level 2: not found among 14 units; level 3: not found among 153 units. Closest by first
   - Handling: The area of interest falls back to an envelope around the retrieved records, padded by a fixed margin.
   - Bearing: Population from neighbouring districts may be counted in the denominator, and parts of the district with no recorded points may be excluded. Coverage shares are for the envelope, not the district.
2. **semantics / WPdx+** status_clean is not two-valued in this district. Values present: Non-Functional (3,234); Functional, not in use (1,137); Functional (4); Abandoned/Decommissioned (3); Non-Functional, dry season (3); Functional, needs repair (1).
   - Handling: Serving status is read from the status_semantics block in handbooks/wpdx.yaml, which treats Functional and Functional, needs repair as serving and everything else as not serving.
   - Bearing: An equality test against the string Functional would score every point in this district as not serving and inflate the gap.
3. **semantics / WPdx+** 3 records carry a status string not listed in the handbook.
   - Handling: Treated as not serving, which is the conservative reading.
   - Bearing: The gap may be overstated by up to these records.
4. **currency / WPdx+** The median record in this district is 6.6 years old (oldest 13.0 years, newest 0.6 years).
   - Handling: Age is reported, not filtered. Uganda district holdings range from a mean of six to over sixteen years, so a staleness filter would delete whole districts rather than clean them.
   - Bearing: Coverage figures describe the surveyed state of the network, not necessarily its state today.
5. **duplication / WPdx+** 605 of 4,987 returned records were removed: flagged duplicate by WPdx.
   - Handling: Removed before any coverage computation, per the cleaning rules declared in the handbook.
   - Bearing: Coverage computed on the raw response would count these records twice or count superseded observations.

## Scoring review

Weighted **7.67** against a floor of 6.50: **issue**. Reviewer: deterministic rules over results.json.

| Dimension | Score | Floor | Weight | Outcome |
|---|---|---|---|---|
| data adequacy | 6.5 | 5.0 | 0.25 | met |
| method fitness | 6.5 | 5.0 | 0.20 | met |
| spatial rigour | 8.0 | 6.0 | 0.25 | met |
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
| coverage arithmetic | PASS | union recount matches at 736,548; across the 10 new sites a per-facility sum would report 605,194 against a true union of 596,234, an overcount of 8,960 |
| geometry | PASS | all 10 sites lie inside the area of interest |
| budget | PASS | 10 sites within the budget of 10 |
| coordinate reference system | PASS | EPSG:32628 (WGS 84 / UTM zone 28N); over 400 sampled pairs the projected distance departs from great-circle by 0.166% at the median and 0.532% at most, or about 5 m on a 1000 m service radius |
| data currency | PASS | median source record is 6.6 years old |
| aggregation sensitivity | PASS | reweighting the demand surface within cells moves the coverage claim from 48.4% to 48.5%, a shift of 0.10% |
| boundary effect | FLAG | the area of interest is an envelope around the retrieved records, not an administrative boundary; 0 of 10 sites lie within one service radius of its edge, nearest 2249 m. Demand and facilities beyond the envelope are unrepresented and the exposure is not well characterised |
| equity | PASS | 100% of newly covered people live in the densest quartile of cells, against 95% district-wide |
| provenance | PASS | 3 sources recorded, all figures traceable |
| cartographic consistency | FLAG | the rendered figures were not reviewed against the account; run the map-reviewer agent over figures.json and pass its verdict with --figure-review |
