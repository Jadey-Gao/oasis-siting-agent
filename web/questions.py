"""Question cards, read from the register rather than written here.

`siting.decisions.REGISTER` already carries, for each decision that belongs to a
person, the question, the unit, the options hint, and the reason it has no
default. This module imports that tuple read-only and assembles a card: the
question, that paragraph, and — where the option can be probed — what each
choice would do to this district's own numbers.

It must never add a question, drop one, or soften the "why this is not a
default" text. That paragraph is the argument the officer is owed, and it is the
one thing in the interview that is not negotiable.

Every card takes free text as well as options. The two are not alternatives: the
option settles the value, and what the officer writes is the reason, which goes
into the record verbatim. A card with options but no free text would collect a
number with no account of it, which is the failure this whole system exists to
prevent.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from siting import decisions as dec

from . import pricing

# The order the interview asks in. Mirrors the checkpoints in
# `skills/siting-run/SKILL.md` — Stage 1 puts data currency, Stage 2 puts radius
# and objective, and the deferred question waits for a plan. Kept as one
# constant so that a change to the skill is a change to this line and nothing
# else.
#
# `budget` leads for a mechanical reason as well as a procedural one: every
# probe below is solved at the budget the officer gives, so nothing can be
# priced until it is settled.
ORDER: tuple[str, ...] = (
    "budget",
    "data_currency_accepted",
    "service_radius_m",
    "coverage_basis",
    "objective",
    "coverage_tolerance",
    "review_floor",
    "equity_accepted",
)

DEFERRED_KEYS = {r.key for r in dec.DEFERRED}


@dataclass(frozen=True)
class Option:
    """One answer the officer can pick, with what it would do."""

    value: Any
    label: str
    note: str = ""              # the register's own word for it: "strict", "JMP-aligned"
    consequence: str = ""       # one sentence, every number from a probe
    evidence: dict[str, Any] = field(default_factory=dict)
    attribution: str = ""       # who took the other decisions in that probe
    probed: bool = False        # False means no run stands behind this option


@dataclass(frozen=True)
class Card:
    key: str
    position: int               # 1-based, over ORDER
    question: str
    why_not_a_default: str
    unit: str
    options_hint: str
    options: tuple[Option, ...] = ()
    free_text: bool = True      # always. See the module docstring.
    numeric: bool = False       # the answer is a number the officer types
    deferred: bool = False      # only real once a plan exists
    caveat: str = ""            # what the probes are, and are not
    warning: str = ""           # degeneracy, or a missing reference run
    recorded: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["options"] = [asdict(o) for o in self.options]
        return d


def _spec(key: str) -> dec.Required:
    for r in dec.REGISTER + dec.DEFERRED:
        if r.key == key:
            return r
    raise KeyError(f"{key} is not a decision the register declares")


def _hint_options(hint: str) -> list[tuple[str, str]]:
    """Split an options hint into (value, note).

    The register writes these for a person to read — "500 (strict) | 1000
    (JMP-aligned)" — so this is prose being read as data, and it is the only
    place in the web layer that happens. A hint that stops splitting on "|"
    degrades to a single option, never to a wrong one.
    """
    out = []
    for part in (p.strip() for p in hint.split("|") if p.strip()):
        m = re.match(r"^(.*?)\s*\((.*)\)$", part)
        out.append((m.group(1).strip(), m.group(2).strip()) if m else (part, ""))
    return out


def _forms(value: Any) -> set[str]:
    """How one value might be written in a hint a person wrote: 1000.0 or 1000."""
    out = {str(value).strip()}
    if isinstance(value, float):
        out.add(f"{value:g}")               # 1000.0 -> "1000", 0.02 -> "0.02"
        if value.is_integer():
            out.add(str(int(value)))
    return out


def _note_for(value: Any, hint: str) -> str:
    """The register's own adjective for a value, when it has one."""
    forms = _forms(value)
    for raw, note in _hint_options(hint):
        if raw.strip() in forms:
            return note
    return ""


def _consequence(key: str, p: pricing.Priced) -> str:
    """One sentence, every figure in it from that probe's results.json."""
    if key == "objective":
        ev = p.evidence
        s = f"Adds {p.newly_covered:,} people."
        if "nearest_working_m_median" in ev:
            s += (f" The sites it chooses sit a median "
                  f"{ev['nearest_working_m_median'] / 1000:.1f} km from the nearest "
                  f"working point, the furthest {ev['nearest_working_m_max'] / 1000:.1f} km.")
        return s
    return (f"{p.covered_today:,} people ({p.covered_share:.1%}) would count as "
            f"already served; the programme would add {p.newly_covered:,}.")


def _priced_card(key: str, position: int, table: pricing.Table) -> Card:
    spec = _spec(key)
    options = tuple(
        Option(value=p.value, label=p.label, note=_note_for(p.value, spec.options_hint),
               consequence=_consequence(key, p), evidence=p.evidence,
               attribution=p.attribution, probed=True)
        for p in table.priced
    )
    return Card(key=key, position=position, question=spec.question,
                why_not_a_default=spec.why_not_a_default, unit=spec.unit,
                options_hint=spec.options_hint, options=options,
                caveat=table.caveat, warning=table.degenerate or "")


def _hint_card(key: str, position: int, *, warning: str = "",
               numeric: bool = False) -> Card:
    spec = _spec(key)
    options = tuple(
        Option(value=raw, label=raw, note=note)
        for raw, note in _hint_options(spec.options_hint)
    )
    return Card(key=key, position=position, question=spec.question,
                why_not_a_default=spec.why_not_a_default, unit=spec.unit,
                options_hint=spec.options_hint, options=options,
                numeric=numeric, deferred=key in DEFERRED_KEYS, warning=warning)


def _tolerance_card(position: int, reference: dict[str, Any] | None) -> Card:
    """What each threshold means in people, on a plan that exists.

    Without a reference run the options are still offered, because the officer
    may hold a governance position regardless — but the card says plainly that
    no figure stands behind them yet.
    """
    if reference is None:
        return _hint_card("coverage_tolerance", position, warning=(
            "No plan has been solved yet, so what each threshold would mean in "
            "people cannot be stated. It is shown once a plan exists."))

    spec = _spec("coverage_tolerance")
    rows = pricing.tolerance_in_people(reference)
    options = tuple(
        Option(value=r["value"], label=r["label"],
               note=_note_for(r["value"], spec.options_hint),
               consequence=r["note"], evidence={"people": r["people"], "inert": r["inert"]},
               probed=False)
        for r in rows
    )
    inert = [r["label"] for r in rows if r["inert"]]
    warning = ""
    if len(inert) == len(rows):
        warning = (
            f"On this plan's numbers every threshold offered ({', '.join(inert)}) "
            f"is larger than the plan's entire gain of "
            f"{reference['plan']['newly_covered']:,} people. No override could "
            "breach any of them, so the guardrail will pass whatever is chosen "
            "and its verdict will carry no information. Record a position on "
            "escalation, not an expectation that this check will bite.")
    elif inert:
        warning = (f"{', '.join(inert)} exceed this plan's entire gain and could "
                   "never be breached.")
    return Card(key="coverage_tolerance", position=position, question=spec.question,
                why_not_a_default=spec.why_not_a_default, unit=spec.unit,
                options_hint=spec.options_hint, options=options, warning=warning)


def _equity_card(position: int, plan: dict[str, Any] | None) -> Card:
    """The seventh question, which only becomes real once a plan exists."""
    warning = ""
    if plan is not None:
        check = next((e for e in plan.get("evaluation", [])
                      if e["check"] == "equity"), None)
        if check:
            warning = f"Measured on this plan: {check['detail']}"
    else:
        warning = ("Not yet askable. The distribution can only be measured on a "
                   "plan, and no plan has been solved.")
    return _hint_card("equity_accepted", position, warning=warning)


def cards(tables: dict[str, pricing.Table] | None = None, *,
          recorded: dict[str, Any] | None = None,
          reference: dict[str, Any] | None = None) -> list[Card]:
    """Every card, in interview order.

    `tables` are the priced options from `pricing.price`. `reference` is a
    results.json to compute the tolerance card and the equity finding from —
    a probe run before a plan exists, the plan itself afterwards.
    """
    tables = tables or {}
    recorded = recorded or {}
    out: list[Card] = []
    for i, key in enumerate(ORDER, start=1):
        if key == "budget":
            card = _hint_card(key, i, numeric=True,
                              warning=pricing.NOT_PRICEABLE["budget"])
        elif key == "coverage_tolerance":
            card = _tolerance_card(i, reference)
        elif key == "equity_accepted":
            card = _equity_card(i, reference)
        elif key in tables and tables[key].priced:
            card = _priced_card(key, i, tables[key])
        else:
            why = pricing.NOT_PRICEABLE.get(key, "")
            card = _hint_card(key, i, warning=why)
        if key in recorded:
            card = Card(**{**card.to_dict(),
                           "options": card.options,
                           "recorded": recorded[key]})
        out.append(card)
    return out


def progress(recorded: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the top bar shows. Required only; the deferred one is not counted."""
    recorded = recorded or {}
    done = sorted(dec.REQUIRED_KEYS & set(recorded))
    outstanding = [k for k in ORDER
                   if k in dec.REQUIRED_KEYS and k not in recorded]
    return {
        "recorded": len(done),
        "required": len(dec.REQUIRED_KEYS),
        "outstanding": outstanding,
        "deferred_outstanding": [k for k in DEFERRED_KEYS if k not in recorded],
        "statement": f"{len(done)} of {len(dec.REQUIRED_KEYS)} recorded",
    }


def _main(argv: list[str]) -> int:
    """python -m web.questions <country> <adm2> <iso3> <budget>"""
    import asyncio
    import sys
    if len(argv) < 4:
        print(_main.__doc__)
        return 2
    base = {"country": argv[0], "adm2": argv[1], "iso3": argv[2], "domain": "water"}
    tables = asyncio.run(pricing.price(base, int(argv[3])))
    for c in cards(tables):
        head = f"[{c.position}/{len(ORDER)}] {c.key}"
        print(f"\n{head}\n{'-' * len(head)}")
        print(f"  {c.question}")
        if c.deferred:
            print("  (deferred: only real once a plan exists)")
        for o in c.options:
            note = f" · {o.note}" if o.note else ""
            print(f"    - {o.label}{note}")
            if o.consequence:
                print(f"        {o.consequence}")
        if c.numeric:
            print("    - (a number the officer types)")
        print("    - ✎ free text, recorded verbatim as the reason")
        if c.warning:
            print(f"    !! {c.warning[:200]}")
    p = progress()
    print(f"\n{p['statement']}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
