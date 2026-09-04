// Page furniture for an evidence bundle. Deliberately plain: this document is
// read for what it contains, not looked at. One ink, one grey, no tinted
// panels, no display type, no stat tiles.

#let ink = rgb("#161616")
#let muted = rgb("#5c5c5c")
#let rule = rgb("#b8b8b8")
#let hair = rgb("#d8d8d8")
#let flag = rgb("#8a5a10")
#let stop = rgb("#8c2f22")

// Typst renders a negative integer with U+2212 MINUS SIGN, not an ASCII hyphen,
// so the sign is taken off the number rather than off the string.
#let thousands(n) = {
  let v = calc.round(n)
  let neg = v < 0
  let s = str(calc.abs(v))
  if s.contains(".") { s = s.split(".").at(0) }
  let out = ()
  let i = s.len()
  while i > 3 {
    out.push(s.slice(i - 3, i))
    i = i - 3
  }
  out.push(s.slice(0, i))
  (if neg { sym.minus } else { "" }) + out.rev().join(",")
}

#let pct(x, digits: 1) = str(calc.round(x * 100, digits: digits)) + "%"

#let hrule(w: 0.5pt, c: rule) = line(length: 100%, stroke: w + c)

// The standing head that opens every exhibit: what it supports, what was
// captured, and by what method. The same three fields every time, so a reader
// can check one exhibit against another without re-reading prose.
#let exhibit-head(id, title, supports, captured, method) = {
  v(4pt)
  hrule(w: 0.9pt, c: ink)
  v(5pt)
  text(size: 11pt, weight: 700)[#id #sym.dash.en #title]
  v(4pt)
  grid(
    columns: (66pt, 1fr),
    row-gutter: 3.5pt,
    text(size: 7.5pt, weight: 700, fill: muted)[SUPPORTS],
    text(size: 8.5pt)[#supports],
    text(size: 7.5pt, weight: 700, fill: muted)[CAPTURED],
    text(size: 8.5pt)[#captured],
    text(size: 7.5pt, weight: 700, fill: muted)[METHOD],
    text(size: 8.5pt)[#method],
  )
  v(6pt)
  hrule(w: 0.4pt, c: hair)
  v(5pt)
}

// Two-column fact list. Values right-aligned so a column of figures reads.
#let facts(rows) = table(
  columns: (1fr, auto),
  align: (left, right),
  stroke: (x, y) => (bottom: 0.3pt + hair),
  inset: (x: 0pt, y: 4pt),
  ..rows.map(r => (text(size: 8.5pt)[#r.at(0)], text(size: 8.5pt)[#r.at(1)])).flatten(),
)

// General evidence table: hairlines, header rule, nothing else.
#let sheet(columns: (), align: (), header: (), rows: (), size: 8pt) = table(
  columns: columns,
  align: align,
  stroke: (x, y) => (bottom: if y == 0 { 0.6pt + ink } else { 0.3pt + hair }),
  inset: (x: 5pt, y: 4pt),
  table.header(..header.map(h => text(size: 7.5pt, weight: 700)[#h])),
  ..rows.map(r => r.map(c => text(size: size)[#c])).flatten(),
)

#let level-mark(level) = {
  let c = if level == "pass" { muted } else if level == "flag" { flag } else { stop }
  text(size: 7.5pt, weight: 700, fill: c)[#upper(level)]
}

#let source-note(body) = text(size: 7.5pt, fill: muted, style: "italic")[#body]

// Verbatim capture. Never set through prose: Typst turns a double hyphen into
// an en dash, which would silently corrupt a query a reader is meant to check.
#let verbatim(s, lang: none) = block(
  width: 100%,
  inset: (x: 7pt, y: 6pt),
  stroke: 0.4pt + hair,
  raw(s, lang: lang, block: true),
)
