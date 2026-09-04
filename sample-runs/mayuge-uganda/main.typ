#import "lib.typ": *

#let d = json("results.json")
#let scope = d.scope
#let base = d.baseline
#let plan = d.plan
#let ex = d.exhibits

#set document(title: scope.domain_title + " " + scope.adm2, author: "OASIS siting agent")

#set page(
  paper: "a4",
  margin: (top: 22mm, bottom: 18mm, x: 20mm),
  header: context {
    if counter(page).get().first() > 1 {
      set text(size: 7pt, fill: muted)
      grid(
        columns: (1fr, auto),
        align(left)[#scope.adm2 #sym.dot.c #scope.country #sym.dot.c #scope.domain_title],
        align(right)[#d.run.id],
      )
      v(-7pt)
      hrule(w: 0.3pt, c: hair)
    }
  },
  footer: context {
    set text(size: 7pt, fill: muted)
    align(center)[#counter(page).display()]
  },
)

#set text(font: ("Libertinus Serif", "Georgia"), size: 9.5pt, fill: ink, lang: "en")
#set par(justify: true, leading: 0.6em, spacing: 0.85em)
#show heading: set text(font: ("Libertinus Serif", "Georgia"))
#set table(stroke: none)
#show raw: set text(font: ("DejaVu Sans Mono", "Consolas"), size: 7.5pt)

// ============================================================ cover
#text(size: 7.5pt, weight: 700, fill: muted)[
  #upper("Siting evidence bundle")
]
#v(3pt)
#text(size: 17pt, weight: 700)[
  #scope.domain_title in #scope.adm2 District, #scope.country
]
#v(8pt)
#hrule(w: 0.9pt, c: ink)
#v(7pt)

#grid(
  columns: (auto, 1fr),
  row-gutter: 3.5pt,
  column-gutter: 12pt,
  text(size: 7.5pt, weight: 700, fill: muted)[SUBJECT],
  text(size: 9pt)[Placement of #str(scope.budget) additional #scope.unit#{if scope.budget > 1 {"s"}} under a fixed budget],
  text(size: 7.5pt, weight: 700, fill: muted)[PREPARED BY],
  text(size: 9pt)[An autonomous analysis agent, from open data, without field verification],
  text(size: 7.5pt, weight: 700, fill: muted)[DECISIONS],
  text(size: 9pt)[#d.at("authorship", default: (statement: "not recorded")).statement],
  text(size: 7.5pt, weight: 700, fill: muted)[REVIEWED BY],
  text(size: 9pt)[#if d.audit.len() > 0 [#d.audit.at(0).actor, whose decisions are recorded at Ex07] else [Not reviewed. No planner overrides were applied to this run.]],
  text(size: 7.5pt, weight: 700, fill: muted)[GENERATED],
  text(size: 9pt)[#d.run.generated_at],
  text(size: 7.5pt, weight: 700, fill: muted)[RUN],
  text(size: 9pt)[#raw(d.run.id)],
  text(size: 7.5pt, weight: 700, fill: muted)[PROVENANCE],
  text(size: 9pt)[#raw(d.run.provenance_hash)],
)

#v(12pt)
#hrule(w: 0.4pt, c: hair)
#v(9pt)

== Statement

#set text(size: 9.5pt)

#let auth = d.at("authorship", default: (:))
#if auth.at("by_agent", default: 0) > 0 [
  #block(width: 100%, inset: (x: 10pt, y: 8pt), stroke: (left: 2pt + flag))[
    #text(size: 9pt)[
      *#str(auth.by_agent) of the judgements this assessment rests on were taken
      by the agent, not by the district.* They are listed at Ex00 with the basis
      on which each was taken. A figure in this statement that depends on one of
      them is conditional on a machine's assumption rather than a recorded
      district position.
    ]
  ]
  #v(8pt)
]

An estimated #thousands(base.population) people live in the area of interest.
#thousands(base.covered) of them, #pct(base.covered_share), live #scope.coverage_rule
of a #scope.unit recorded as serving. #thousands(base.uncovered) people, #pct(1 - base.covered_share)
of the district, do not (Ex01, Ex02, Ex03).

Of the #thousands(scope.points_total) #scope.unit records held for this district,
#thousands(scope.points_working) are recorded as serving and #thousands(scope.points_broken)
are not (Ex01). The median record is #str(scope.median_record_age_years) years old,
so the figures above describe the surveyed state of the network rather than its
state today (Ex10).

#thousands(base.candidates) candidate locations were evaluated (Ex04). Selecting
#str(plan.sites.len()) of them by #scope.at("objective_label", default: "the objective recorded for this run")
brings a further #thousands(plan.newly_covered) people inside the service radius,
raising coverage to #pct(plan.covered_share) (Ex05, Ex06).
#if scope.at("objective", default: "") == "max_coverage" [
  Returns fall away quickly: the first site reaches
  #thousands(d.curve.at(0).marginal) people and the last
  #thousands(d.curve.last().marginal), so the number of sites to build is a
  decision that should be taken on the curve at Ex05 rather than on a round
  number.
] else [
  The first site reaches #thousands(d.curve.at(0).marginal) people and the last
  #thousands(d.curve.last().marginal). This objective does not order sites by the
  population each reaches, so the curve at Ex05 records what each site adds rather
  than a diminishing return, and the number of sites to build cannot be read off
  it in the same way.
]

#if d.audit.len() > 0 [
  #d.audit.len() planner overrides were applied. They forgo
  #thousands(calc.abs(d.guardrail.people_forgone)) people,
  #pct(calc.abs(d.guardrail.relative_loss)) of the coverage the unmodified plan
  would have reached. Each override, the reason given for it and its individual
  cost are recorded at Ex07. No override was blocked.
] else [
  No planner overrides were applied. The recommendation at Ex06 is the unmodified
  output of the selection method at Ex05.
]

#let rejects = d.evaluation.filter(f => f.level == "reject")
#let flags = d.evaluation.filter(f => f.level == "flag")
#if flags.len() > 0 [
  #flags.len() of the #d.evaluation.len() independent checks at Ex08 returned a
  flag rather than a pass: #flags.map(f => f.check).join(", "). The flagged
  findings are reproduced in full at Ex08 and are not resolved in this bundle.
] else [
  All #d.evaluation.len() independent checks at Ex08 returned a pass.
]

#if d.anomalies.len() > 0 [
  #d.anomalies.len() properties of the source registers would have changed a
  coverage figure had they been handled differently. Each is recorded at Ex10
  with what was observed and what was done about it. None were corrected
  silently.
]

#v(10pt)
#source-note[
  Recommendations in this bundle are advisory. Land availability, tenure, water
  table, community consent and construction feasibility are outside the data used
  here and inside the judgement of the officer reviewing it. No figure in this
  bundle is asserted without an exhibit number after it.
]

#pagebreak()

// ============================================================ index
#text(size: 12pt, weight: 700)[Index of exhibits]
#v(6pt)

#sheet(
  columns: (auto, 1fr, 1.15fr),
  align: (left, left, left),
  header: ([Ex], [Content], [What it supports]),
  rows: ex.map(e => (e.id, e.title, e.supports)),
)

#v(10pt)
#text(size: 10pt, weight: 700)[Verification]
#v(3pt)
#set text(size: 8.5pt)

Every retrieval in this bundle is recorded with its endpoint, the query as
executed, the time of retrieval and the record counts before and after cleaning
(Ex01, Ex02, Ex11). Cleaning rules are declared per source in the handbook files
that accompany this bundle and are applied in order, each reporting its own drop
count, so no record leaves the pipeline unaccounted for.

Coverage is recomputed independently of the solver by a separate process before
this bundle is produced, and that process can prevent it being produced at all
(Ex08). Coverage is always the union over serving locations; a per-location sum
double counts every settlement reachable from two of them, and Ex08 reports what
that sum would have claimed.

Properties of the source data that would change a figure if read differently are
recorded at Ex10 rather than handled silently. Where the agent's own source
handbook was found to be incomplete, that is recorded there too.

No figure in this bundle was estimated, interpolated or supplied from memory. A
figure is either computed from a recorded retrieval or it is absent.

#pagebreak()

// ============================================================ exhibits
#set text(size: 9.5pt)

#for e in ex [
  #exhibit-head(e.id, e.title, e.supports, e.captured, e.method)

  #for b in e.blocks [
    #if b.type == "query" [
      #verbatim(b.text)
      #v(4pt)
    ] else if b.type == "command" [
      #verbatim(b.text, lang: "sh")
      #v(4pt)
    ] else if b.type == "yaml" [
      #verbatim(b.text, lang: "yaml")
      #v(4pt)
    ] else if b.type == "counts" [
      #facts(b.rows)
      #v(6pt)
    ] else if b.type == "drops" [
      #if b.rows.len() > 0 [
        #text(size: 8pt, weight: 700, fill: muted)[RECORDS DISCARDED, BY RULE]
        #v(2pt)
        #facts(b.rows)
        #v(6pt)
      ]
    ] else if b.type == "note" [
      #if b.text != "" [
        #text(size: 8.5pt)[#b.text]
        #v(5pt)
      ]
    ] else if b.type == "licence" [
      #source-note[Licence: #b.text]
      #v(6pt)
    ] else if b.type == "image" [
      #align(center)[#image(b.path, height: eval(b.height))]
      #v(3pt)
      #source-note[#b.caption]
      #v(6pt)
    ] else if b.type == "curve" [
      #sheet(
        columns: (auto, auto, auto, auto),
        align: (right, right, right, right),
        header: ([Sites built], [Newly covered], [Cumulative], [Share of district]),
        rows: b.rows,
      )
      #v(3pt)
      #source-note[#b.caption]
      #v(6pt)
    ] else if b.type == "sites" [
      #sheet(
        columns: (auto, auto, auto, auto, auto, auto),
        align: (right, left, left, left, right, right),
        header: ([No.], [Site], [Type], [Location (WGS 84)], [Newly covered], [Nearest serving]),
        rows: b.rows,
      )
      #v(6pt)
    ] else if b.type == "rationales" [
      #text(size: 8pt, weight: 700, fill: muted)[BASIS FOR EACH SITE]
      #v(3pt)
      #for r in b.rows [
        #grid(columns: (38pt, 1fr), column-gutter: 6pt,
          text(size: 8pt, weight: 700)[#r.at(0)],
          text(size: 8pt)[#r.at(1)])
        #v(2pt)
      ]
      #v(4pt)
    ] else if b.type == "audit" [
      #sheet(
        columns: (auto, auto, 1fr, auto),
        align: (left, left, left, right),
        header: ([Verb], [Target], [Reason given], [Coverage cost]),
        rows: b.rows,
      )
      #v(6pt)
    ] else if b.type == "verdict" [
      #text(size: 8.5pt, weight: 700)[#b.text]
      #v(6pt)
    ] else if b.type == "checks" [
      #sheet(
        columns: (auto, auto, 1fr),
        align: (left, left, left),
        header: ([Check], [Result], [Finding]),
        rows: b.rows.map(r => (r.at(0), level-mark(r.at(1)), r.at(2))),
      )
      #v(6pt)
    ] else if b.type == "decisions" [
      #sheet(
        columns: (auto, auto, auto, 1fr),
        align: (left, left, left, left),
        header: ([Decision], [Value], [Recorded by], [Reason given]),
        rows: b.rows.map(r => (
          raw(r.at(0)), r.at(1),
          if r.at(2) == "AGENT" { text(fill: flag, weight: 700)[agent] } else { r.at(2) },
          r.at(3))),
        size: 7.5pt,
      )
      #v(6pt)
    ] else if b.type == "gates" [
      #sheet(
        columns: (auto, auto, auto, 1fr),
        align: (left, left, left, left),
        header: ([Gate], [Outcome], [Decided by], [Reason recorded]),
        rows: b.rows.map(r => (r.at(0), level-mark(if r.at(1) == "allowed" { "pass" } else { "flag" }),
                               r.at(2), r.at(3))),
      )
      #v(6pt)
    ] else if b.type == "review" [
      #sheet(
        columns: (auto, auto, auto, auto, auto, 1fr),
        align: (left, right, right, right, left, left),
        header: ([Dimension], [Score], [Floor], [Weight], [], [Basis]),
        rows: b.rows,
        size: 7.5pt,
      )
      #v(6pt)
    ] else if b.type == "sensitivity" [
      #sheet(
        columns: (1fr, auto, auto, auto),
        align: (left, right, right, right),
        header: ([Scenario], [Covered], [Share], [Against the base case]),
        rows: b.rows,
      )
      #v(6pt)
    ] else if b.type == "anomalies" [
      #for (i, r) in b.rows.enumerate() [
        #text(size: 8pt, weight: 700)[#str(i + 1). #upper(r.at(0)) #sym.dot.c #r.at(1)]
        #v(1pt)
        #grid(columns: (58pt, 1fr), row-gutter: 2.5pt, column-gutter: 6pt,
          text(size: 7.5pt, fill: muted)[Observed],  text(size: 8pt)[#r.at(2)],
          text(size: 7.5pt, fill: muted)[Handling],  text(size: 8pt)[#r.at(3)],
          text(size: 7.5pt, fill: muted)[Bearing],   text(size: 8pt)[#r.at(4)])
        #v(6pt)
      ]
    ] else if b.type == "sources" [
      #sheet(
        columns: (1fr, auto, auto),
        align: (left, left, right),
        header: ([Source], [Licence], [Accessed]),
        rows: b.rows,
        size: 7.5pt,
      )
      #v(6pt)
    ]
  ]

  #v(8pt)
]
