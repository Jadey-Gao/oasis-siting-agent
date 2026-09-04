# Skill: spatial siting analysis

A decision framework, not a script. It states what must be considered, what
guardrails apply, and what the output must contain. The order of operations is
left to the executing agent, which knows the context; the guarantees are left to
the harness, which does not.

Adapted from the Spatial Analysis Skill Unit of NORA (Zhou, Huang, Ning, Wu, Li
and Zhang, 2026, arXiv:2605.02092), which encodes the same idea for spatial
regression and exploratory analysis. The guardrails below are its list of common
spatial mistakes, restated for a siting problem.

---

## 1. Classify the objective

Siting problems are one of NORA's nine analytical objectives: **accessibility**.
Before anything else, settle which of these the question actually is, because
they take different models and answer to different criteria.

| Question | Model | Answers to |
|---|---|---|
| Where do we put the next *b* facilities to reach the most people? | Maximum covering (MCLP) | Coverage |
| How few facilities cover everyone? | Location set covering (LSCP) | Completeness |
| Where do we put *b* so the worst-served person is least badly served? | p-centre | Equity of the tail |
| Where do we put *b* to minimise total travel? | p-median | Aggregate burden |

Default here is MCLP under a fixed budget, because a district has a capital
programme with a number in it. When the brief is about the worst-served rather
than the most people, p-centre is the correct model and MCLP is the wrong one.
Report which was used and why.

## 2. Data readiness

Before the demand surface or the candidate set exists:

- **CRS**. Pick a projected CRS for the area of interest and do every distance,
  buffer and area computation in it. Record which, and why that one.
- **Geometry validity**. Repair invalid polygons; drop empty geometries and
  report how many.
- **Duplicates and supersession**. A register that returns a record does not
  necessarily hold a distinct, current observation. Apply the source's own
  duplicate and latest flags before counting anything.
- **Field semantics**. Never assume a status field is two-valued. Enumerate the
  values actually present and map them explicitly; record any value the handbook
  does not declare.
- **Currency**. Report the age distribution of the records. Do not silently
  filter on age: in registers of this kind a staleness filter deletes whole
  districts rather than cleaning them.

## 3. Demand surface

- State the population source, its native resolution, and any aggregation.
- Aggregation must stay **coarser than the source and finer than the service
  radius**, otherwise it biases coverage.
- Preserve the total: a sum-preserving rebinning, not a resample that
  interpolates.
- Weighting is domain-specific: population alone for water, population times
  prevalence for disease burden, population times exposure for monitoring.

## 4. Candidate set

- State where candidates come from. A recommendation is only as defensible as
  the set it was chosen from.
- Admit both rehabilitation of existing assets and new construction where both
  are real options, and label which is which.
- Lay out any lattice in the projected CRS so spacing is uniform on the ground.
- Remove candidates that add nothing before solving, and say how many.

## 5. Coverage rule

- **Coverage is a union over facilities, never a sum.** A settlement within
  reach of two facilities is served once. Summing per facility double counts.
- Straight-line distance is a stand-in for travel. Where a friction surface or
  road network is available, use travel time and say so; where it is not, state
  plainly that real walking distances exceed the radius used.
- State the radius and its provenance. A 1 km walk stands in for the 30 minutes
  the JMP uses for a basic service; that link should be in the report, not in
  the analyst's head.

## 6. Required diagnostics

None of these are optional, and each has a place in the output.

| Diagnostic | Why | Failure mode it catches |
|---|---|---|
| **Independent coverage recount** | The solver should not certify itself | Per-facility summing, off-by-one on the baseline |
| **CRS disagreement** | Confirms the projection is not load-bearing | Area too wide for one UTM zone |
| **MAUP sensitivity** | The demand surface is a rebinning | A claim that only holds at one aggregation |
| **Boundary effect** | The area of interest has an edge | Sites near the edge scored against unseen demand |
| **Equity** | Coverage maximisation favours density | A plan that is efficient and unjust |
| **Budget** | | Solver placed more or fewer than asked |

## 7. Guardrails

Violating any of these invalidates the result. They are checked by the harness,
not left to judgement.

1. **Never compute distance from decimal degrees.** Project first.
2. **Never sum coverage across facilities.** Take the union.
3. **Never filter a register on age without reporting what that removed.**
4. **Never treat an unrecognised categorical value as the favourable case.**
   Unknown status is not-serving, not serving.
5. **Never present an envelope of the input records as an administrative area.**
   If the boundary is not the real boundary, say so and test the edge.
6. **Never let the model that generates a plan also certify it.**
7. **Never state a figure that does not resolve to a recorded retrieval.**
8. **Never claim causality.** A siting analysis says where coverage would
   increase, not what would then happen to health outcomes.

## 8. Output contract

Every run emits:

- `results.json` — the sole input to every document produced, so two documents
  from one run cannot disagree.
- A provenance record per retrieval: endpoint, query as executed, timestamp,
  raw and clean counts, drops by rule, licence.
- An anomaly record per property of the source data that would have changed a
  figure had it been read differently, with what was observed, what was done,
  and what a reader must hold in mind.
- A decision record per human override: the verb, the reason, and the cost in
  people.
- A manifest whose replay reproduces the run.
