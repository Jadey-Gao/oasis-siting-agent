"""One interview, on disk.

An interview holds an officer's afternoon. It must survive a restarted server,
a closed laptop, and the API being briefly unavailable, because the alternative
is asking someone to make six considered judgements a second time.

What is durable is not this object. It is the two YAML files the interview
writes — those are the record, and they are readable without this code ever
running again. `session.json` holds what is needed to carry on the conversation:
the transcript, the decisions so far, and the message history the model needs to
remember what was said.

The transcript is the thing worth keeping even when nothing else is. Every
reason in `decisions.yaml` points at an utterance in it, so the file and the
conversation that produced it can always be checked against each other.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import claude, pricing, questions, runner
from .runner import REPO

SESSIONS = REPO / "sessions"


def _jsonable(obj: Any) -> Any:
    """Message content may hold SDK block objects; store them as plain data.

    Thinking blocks in particular have to come back unchanged on the next turn,
    so they are round-tripped rather than dropped.
    """
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", exclude_none=True)
    if isinstance(obj, list):
        return [_jsonable(o) for o in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj


class Session:
    """An interview, its directory, and the ability to pick it up again."""

    def __init__(self, sid: str, base: dict[str, Any], officer: str) -> None:
        self.id = sid
        self.base = base
        self.officer = officer
        self.dir = SESSIONS / sid
        self.dir.mkdir(parents=True, exist_ok=True)
        self.created = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.iv = claude.Interview(base, self.dir, officer)

    # --- lifecycle ------------------------------------------------------- #

    @classmethod
    def create(cls, base: dict[str, Any], officer: str) -> "Session":
        if not claude.available():
            raise claude.Unavailable(
                "no ANTHROPIC_API_KEY and no stored profile; an interview needs "
                "the agent to conduct it and there is no other way to open one")
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        # adm2 is not known yet when the officer has only typed a place in their
        # own words — the agent resolves it in the interview's opening turn. The
        # directory name is just an identifier, so a slug of whatever is known
        # (the raw text, before resolution) is good enough; it is never read back.
        named = base.get("adm2") or base.get("where") or "session"
        slug = named.strip().lower().split(",")[0].replace(" ", "-") or "session"
        s = cls(f"{slug}-{stamp}-{uuid.uuid4().hex[:6]}", base, officer)
        s.save()
        return s

    @classmethod
    def load(cls, sid: str) -> "Session | None":
        f = SESSIONS / sid / "session.json"
        if not f.exists():
            return None
        raw = json.loads(f.read_text(encoding="utf-8"))
        s = cls(sid, raw["scope"], raw["officer"])
        s.created = raw.get("created", s.created)
        s.iv.utterances = [claude.Utterance(**u) for u in raw.get("utterances", [])]
        s.iv.recorded = raw.get("recorded", {})
        s.iv.overrides = raw.get("overrides", [])
        s.iv.messages = raw.get("messages", [])
        s.iv.last_run = raw.get("last_run")
        s.iv.sdk_session_id = raw.get("sdk_session_id")
        return s

    def save(self) -> None:
        (self.dir / "session.json").write_text(json.dumps({
            "id": self.id, "created": self.created, "scope": self.base,
            "officer": self.officer,
            "utterances": [asdict(u) for u in self.iv.utterances],
            "recorded": self.iv.recorded,
            "overrides": self.iv.overrides,
            "last_run": self.iv.last_run,
            "messages": _jsonable(self.iv.messages),
            "sdk_session_id": self.iv.sdk_session_id,
        }, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- what the page renders ------------------------------------------- #

    def review(self) -> dict[str, Any] | None:
        """The verdict on the last run, for the ledger — read fresh, never cached."""
        if not self.iv.last_run:
            return None
        r = runner.results(Path(self.iv.last_run["run_dir"]))["review"]
        return {"weighted": r["weighted"], "accept_at": r["accept_at"],
               "decision": r["decision"]}

    def evaluation(self) -> list[dict[str, Any]] | None:
        """The guardrail checks — equity, aggregation sensitivity, boundary
        exposure, cartographic consistency and the rest — read fresh, never
        cached. This is the same list `siting/exhibits.py` renders into the
        PDF; showing it here means the web interview surfaces exactly what
        the document will say, not a separate account of it. A map-reviewer
        verdict, once attached via `figure_review`, arrives here too as the
        "cartographic consistency" entry — it does not need a panel of its
        own."""
        if not self.iv.last_run:
            return None
        doc = runner.results(Path(self.iv.last_run["run_dir"]))
        return [{"check": c["check"], "level": c["level"], "detail": c["detail"]}
               for c in doc.get("evaluation", [])]

    def state(self) -> dict[str, Any]:
        p = questions.progress(self.iv.recorded)
        return {
            "id": self.id,
            "scope": self.base,
            "resolved": bool(self.base.get("iso3")),
            "officer": self.officer,
            "conducted_by": "the agent, following skills/siting-run/SKILL.md",
            "transcript": self.iv.transcript(),
            "progress": p,
            "recorded": {k: {"value": v["value"],
                             "reason": v["answer"]["reason"],
                             "reason_from": v["reason_from"]}
                         for k, v in self.iv.recorded.items()},
            "overrides": self.iv.overrides,
            "last_run": self.iv.last_run,
            "review": self.review(),
            "evaluation": self.evaluation(),
            "decisions_yaml": self.decisions_yaml(),
        }

    def decisions_yaml(self) -> str:
        f = self.dir / "decisions.yaml"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def overrides_yaml(self) -> str:
        f = self.dir / "overrides.yaml"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    # --- the card --------------------------------------------------------- #

    async def _all_cards(self) -> dict[str, Any]:
        """Every card, priced against this district's own data where possible.

        Pricing is cache-only in the common case: once `budget` is recorded,
        the agent's own `price_options` tool calls will usually have already
        warmed the exact cache entry `pricing.price` reads here.
        """
        tables: dict[str, pricing.Table] = {}
        if "budget" in self.iv.recorded and self.base.get("iso3"):
            tables = await pricing.price(self.base,
                                         int(self.iv.recorded["budget"]["value"]))
        reference = (runner.results(Path(self.iv.last_run["run_dir"]))
                    if self.iv.last_run else None)
        return {c.key: c for c in
               questions.cards(tables, recorded=self.iv.recorded, reference=reference)}

    async def card_for(self, key: str) -> dict[str, Any] | None:
        """One named card, keyed to what the agent's `present_options` call
        declared — never guessed, so it cannot disagree with what was asked."""
        cards = await self._all_cards()
        c = cards.get(key)
        return c.to_dict() if c else None


# In-process registry. A session not held here is still on disk and is loaded
# on demand, so a restarted server does not lose an interview.
_open: dict[str, Session] = {}


def get(sid: str) -> Session | None:
    if sid in _open:
        return _open[sid]
    s = Session.load(sid)
    if s is not None:
        _open[sid] = s
    return s


def create(base: dict[str, Any], officer: str) -> Session:
    s = Session.create(base, officer)
    _open[s.id] = s
    return s


def listing(limit: int = 25) -> list[dict[str, Any]]:
    out = []
    for f in sorted(SESSIONS.glob("*/session.json"),
                    key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        raw = json.loads(f.read_text(encoding="utf-8"))
        out.append({"id": raw["id"], "created": raw.get("created"),
                    "district": raw["scope"]["adm2"],
                    "recorded": len(raw.get("recorded", {}))})
    return out
