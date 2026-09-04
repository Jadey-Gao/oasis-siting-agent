// Page furniture for a government-style assessment report.
//
// The conventions here follow the layout grammar of federal assessment
// publications: a banded cover, a document-control page, chapter headings in
// small capitals, rounded callout panels, and tables whose caption sits above a
// solid header row. No agency seal, wordmark or colour scheme is reproduced;
// this document carries its own identity and does not represent any government.

#let navy = rgb("#1b3a5c")
#let band = rgb("#12554f")      // deliberately not the green of any agency cover
#let ink = rgb("#1a1a1a")
#let muted = rgb("#5c5c5c")
#let hair = rgb("#c4ccc9")
#let shade = rgb("#eef2f1")
#let flag = rgb("#8a5a10")
#let stop = rgb("#8c2f22")

// Franklin Gothic where it exists, then progressively more common fallbacks, so
// the report compiles on a machine that has none of them.
#let sans = ("Franklin Gothic Book", "Public Sans", "Segoe UI", "Arial", "Libertinus Sans")
#let sans-bold = ("Franklin Gothic Medium", "Public Sans", "Segoe UI", "Arial", "Libertinus Sans")

#let thousands(n) = {
  let v = calc.round(n)
  let neg = v < 0
  let s = str(calc.abs(v))
  if s.contains(".") { s = s.split(".").at(0) }
  let out = ()
  let i = s.len()
  while i > 3 { out.push(s.slice(i - 3, i)); i = i - 3 }
  out.push(s.slice(0, i))
  (if neg { sym.minus } else { "" }) + out.rev().join(",")
}

#let pct(x, digits: 1) = str(calc.round(x * 100, digits: digits)) + "%"

// Fixed decimals. Typst drops trailing zeros, so a column of distances or
// coordinates comes out ragged unless the places are padded back on.
#let fixed(x, places) = {
  let s = str(calc.round(x, digits: places))
  if places == 0 { return s }
  if not s.contains(".") { s = s + "." }
  let have = s.split(".").at(1).len()
  s + "0" * (places - have)
}

#let km1(m) = fixed(m / 1000, 1) + " km"
#let coord(lat, lon) = fixed(lat, 4) + ", " + fixed(lon, 4)

// Rounded panel used for material a reader must not miss. Titled, centred,
// outlined rather than filled, exactly as in the federal guides.
#let callout(title, body) = block(
  width: 100%,
  radius: 12pt,
  stroke: 1.1pt + navy,
  inset: (x: 13pt, y: 11pt),
  breakable: false,
  [
    #align(center)[#text(font: sans-bold, size: 9.5pt, weight: 700)[#title]]
    #v(5pt)
    #set text(font: sans, size: 9pt)
    #body
  ],
)

// The styled table body only. Numbering and caption come from a real Typst
// figure so that the List of Tables builds itself and cross-references work.
#let gov-tbl(columns: (), align: (), header: (), rows: (), size: 8.5pt) = table(
  columns: columns,
  align: align,
  stroke: 0.5pt + hair,
  inset: (x: 6pt, y: 5pt),
  fill: (x, y) => if y == 0 { navy } else if calc.odd(y) { white } else { shade },
  table.header(..header.map(h => text(font: sans-bold, size: 8pt, weight: 700, fill: white)[#h])),
  ..rows.map(r => r.map(c => text(font: sans, size: size)[#c])).flatten(),
)

#let level-mark(level) = {
  let c = if level == "pass" { navy } else if level == "flag" { flag } else { stop }
  text(font: sans-bold, size: 7.5pt, weight: 700, fill: c)[#upper(level)]
}

#let verbatim(s, lang: none) = block(
  width: 100%,
  inset: (x: 7pt, y: 6pt),
  fill: shade,
  stroke: 0.4pt + hair,
  raw(s, lang: lang, block: true),
)

#let source-line(body) = text(font: sans, size: 7.5pt, fill: muted)[#body]
