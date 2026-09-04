"""Answers to YAML. The session's real output.

Two files, both in the shape `siting` already validates:

    sessions/<id>/decisions.yaml    key, value, decided_by, reason
    sessions/<id>/overrides.yaml    verb, site, reason, actor, and the verb's fields

**Nothing here reimplements validation.** A written file is loaded straight back
through `siting.decisions.Register.load` and `siting.overrides.load` — the very
code that will consume it during the run — and whatever they refuse, this module
refuses. Two validators for one file format is one validator too many, and the
second one is always the one that drifts.

The rules that matter more than the code:

- `reason` is the officer's own words. It is not summarised, translated in
  place, or tidied. Where the officer wrote in another language, both are kept
  and the original is the one the report prints. Only surrounding whitespace is
  removed, because whitespace is not part of what they said.
- Long reasons are written as literal blocks (`|-`), never folded (`>-`).
  Folding re-flows line breaks into spaces, which is a change to the text. A
  hand-written file may fold; a file written on someone's behalf may not.
- `decided_by` is a person's name and role, supplied by them. In automatic mode
  it is the agent, and the register marks it as such.
- A partial file is legitimate: `pricing.py` writes one key at a time and lets
  `--mode auto` fill the rest. So writing does not require completeness; it
  reports what is still outstanding and lets the caller decide.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from siting import decisions as dec
from siting import overrides as ov

# Read from the register, never restated. If a decision is added there, it
# becomes writable here with no edit.
KEYS: frozenset[str] = frozenset(
    {r.key for r in dec.REGISTER} | {r.key for r in dec.DEFERRED}
)

# The one thing the register does not declare is a type: it carries a `unit`
# ("metres", "facilities") but not whether a value is a number. The CLI coerces
# with float()/int() when it reads them, so a quoted string would survive, but a
# report reading `value: "1000"` looks like a mistake. If `Required` ever gains a
# type field, delete this.
NUMERIC: dict[str, type] = {
    "service_radius_m": float,
    "budget": int,
    "coverage_tolerance": float,
}

# Not a judgement about what counts as a good reason — that is the officer's and
# the reviewer's business. These are strings that mean "I did not answer".
PLACEHOLDERS = {"", "-", "--", "n/a", "na", "none", "null", "nil", "tbd",
                "test", "testing", "asdf", "x", "xx", "?", "??", "."}


class Refused(Exception):
    """What will not be written, and why."""


@dataclass(frozen=True)
class Answer:
    """One decision as the interview collected it."""

    key: str
    value: Any
    decided_by: str
    reason: str
    reason_original: str | None = None   # if the officer wrote in another language
    language: str | None = None


@dataclass(frozen=True)
class Written:
    """What landed on disk, and what is still missing from it."""

    path: Path
    recorded: tuple[str, ...]
    outstanding: tuple[str, ...]   # required decisions with nothing recorded yet

    @property
    def complete(self) -> bool:
        return not self.outstanding


class _Dumper(yaml.SafeDumper):
    """Literal blocks for anything long enough to wrap, so text is never re-flowed."""


def _represent_str(dumper: yaml.SafeDumper, data: str):
    style = "|" if ("\n" in data or len(data) > 72) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _represent_str)


def _clean(answer: Answer) -> dict[str, Any]:
    """One answer to one YAML entry, refusing what `siting` would refuse anyway."""
    key = answer.key.strip()
    if key not in KEYS:
        raise Refused(f"{key!r} is not a decision the register declares")

    who = (answer.decided_by or "").strip()
    if not who:
        raise Refused(f"{key}: a decision needs the name of whoever made it")

    reason = (answer.reason or "").strip()
    if reason.lower() in PLACEHOLDERS:
        raise Refused(f"{key}: {reason!r} is not a reason")

    value = answer.value
    if key in NUMERIC and not isinstance(value, bool):
        try:
            value = NUMERIC[key](value)
        except (TypeError, ValueError) as exc:
            raise Refused(f"{key}: {value!r} is not a number") from exc

    entry: dict[str, Any] = {
        "key": key,
        "value": value,
        "decided_by": who,
        "reason": reason,
    }
    # Kept beside the reason, never in place of it. The register ignores extra
    # fields; the report prints `reason`, which is the original.
    if answer.reason_original and answer.reason_original.strip() != reason:
        entry["reason_original"] = answer.reason_original.strip()
    if answer.language:
        entry["language"] = answer.language
    return entry


def _header(title: str, note: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# {title}", "#"]
    lines += [f"# {ln}" for ln in note.strip().splitlines()]
    lines += ["#", f"# Written by the siting interview, {stamp}.", ""]
    return "\n".join(lines)


def write_decisions(path: str | Path, answers: Iterable[Answer],
                    *, mode: str = "manual", district: str = "") -> Written:
    """Write a decisions file, then prove it by loading it back.

    Partial files are allowed and are how option pricing works. `outstanding`
    says which required decisions still have nothing recorded against them; the
    caller decides whether that matters.
    """
    answers = list(answers)
    entries = [_clean(a) for a in answers]

    seen: set[str] = set()
    for e in entries:
        if e["key"] in seen:
            raise Refused(f"{e['key']} recorded twice; a decision has one answer")
        seen.add(e["key"])

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.dump({"decisions": entries}, Dumper=_Dumper, sort_keys=False,
                     allow_unicode=True, width=78, indent=2)
    p.write_text(
        _header(
            f"Decisions for the {district or 'district'} siting assessment.",
            "Each of these is a judgement about values, not a technical parameter.\n"
            "Every entry carries the name of whoever made it and their reason, and\n"
            "both are printed in the assessment beside the figure they produced.\n"
            "The reason is the officer's own words, recorded verbatim.",
        ) + body,
        encoding="utf-8",
    )

    # The proof. Whatever the run's own loader refuses, we refuse.
    try:
        register = dec.Register.load(p, mode=mode)
    except (ValueError, FileNotFoundError) as exc:
        raise Refused(f"the file written would be rejected by the run: {exc}") from exc

    recorded = tuple(k for k in seen if register.has(k))
    missing = tuple(sorted(dec.REQUIRED_KEYS - set(recorded)))
    return Written(path=p, recorded=tuple(sorted(recorded)), outstanding=missing)


def write_overrides(path: str | Path, items: Iterable[dict[str, Any]],
                    *, district: str = "") -> Written:
    """Write an overrides file, validating each entry by building the real thing.

    `siting.overrides.Override.__post_init__` rejects an unknown verb and a
    missing reason, and its signature rejects an unknown field. Constructing one
    here means an override that would fail mid-run cannot reach disk.
    """
    built: list[ov.Override] = []
    for item in items:
        try:
            built.append(ov.Override(**item))
        except (TypeError, ValueError) as exc:
            raise Refused(f"override rejected: {exc}") from exc

    entries = []
    for o in built:
        e = {"verb": o.verb, "actor": o.actor, "reason": o.reason.strip(), "ts": o.ts}
        for f in ("site", "candidate", "equity_weight", "radius_m", "budget"):
            v = getattr(o, f)
            if v is not None:
                e[f] = v
        if o.verb == "VETO":
            e["veto_radius_m"] = o.veto_radius_m
        entries.append(e)

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.dump(entries, Dumper=_Dumper, sort_keys=False,
                     allow_unicode=True, width=78, indent=2)
    p.write_text(
        _header(
            f"Planner overrides for {district or 'this district'}.",
            "Four verbs, each with a mandatory reason. Every one is priced against\n"
            "the unconstrained plan and the cost is printed in the report. An\n"
            "override is never blocked; it is recorded and costed.",
        ) + body,
        encoding="utf-8",
    )

    try:
        ov.load(p)
    except (TypeError, ValueError) as exc:
        raise Refused(f"the file written would be rejected by the run: {exc}") from exc

    return Written(path=p, recorded=tuple(o.verb for o in built),
                   outstanding=())
