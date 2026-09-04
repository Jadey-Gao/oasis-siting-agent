"""The agent, and the five tools that are the whole of what it can do.

The skill is the agent's instruction; `prompts.py` loads it. This module supplies
the hands. Which is to say: **the guarantees are in the shape of the tools, not
in what the prompt asks for.** A model can be talked out of an instruction. It
cannot be talked into a capability it was not given.

Three shapes carry the weight.

`record_decision` does not take a reason. It takes the id of something the
officer actually said, and the harness copies that text into the record
verbatim. The model may point at an utterance; it may not author one. This is
what turns "the report prints the officer's own words" from a promise into a
property. If the model wants a better reason it has to go and ask for one.

`price_options` returns figures from real probe runs, not from the model. Every
number the agent quotes has a run behind it.

`run_assessment` is the CLI. If a decision is outstanding it exits 4 and this
tool hands the refusal straight back, so the agent learns what is missing from
the same text a person would have read. There is no path through this module
that produces an assessment from an incomplete register.

Everything else — what to ask first, when to press for a better answer, how to
explain a trade — comes from the skill. That is the half the web layer is not
allowed to write.

Without a credential the agent is unavailable and `Unavailable` is raised.
There is no fallback interviewer: an interview needs the agent to conduct it,
so `session.Session.create` refuses to open one rather than start something it
cannot finish. A turn already in progress that hits a transient failure (a busy
API, not a missing credential) is retried instead — see `MODEL_RETRIES`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import random
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from siting import decisions as dec

from . import emit, pricing, prompts, questions, runner
from .runner import REPO, RunSpec

MODEL = "claude-opus-5"
MAX_TOKENS = 16000       # thinking tokens count against this too
MAX_TOOL_TURNS = 12          # one officer turn should not become an unbounded loop
# 529 from this API arrives in bursts rather than as a steady outage: a trivial
# request can fail in the same second a large one succeeds. Patience is
# therefore worth more than cleverness here, and an interview holding an
# officer's attention is worth waiting through a bad minute for.
MODEL_RETRIES = 6            # on top of the anthropic package's own, for 429 and 5xx
RETRY_BACKOFF = (2, 5, 10, 20, 30, 45)

# Which engine conducts the interview: `legacy` calls the raw Messages API and
# hand-dispatches tool_use blocks; `sdk` runs the Claude Agent SDK, the only
# engine that can actually delegate to a subagent. A rollback switch, not a
# preference — see the migration plan for why both stay live for now.
ENGINE = os.environ.get("SITING_WEB_ENGINE", "legacy")

# Slice 2 of the migration: all four subagents delegate for real. Giving
# data-scout/spatial-analyst their declared Bash access meant first porting
# the load-bearing half of harness/hooks/pre_tool_use.sh into `_gate_tools`
# below — settings.json's own deny rules were confirmed by testing not to be
# inherited just because cwd points at this repo. The other half of that
# spike's punch list (concurrency safety of the shared hook log files under
# multiple simultaneous web sessions) is deliberately not addressed: this is
# a single-operator demo, not a multi-tenant deployment.
SDK_AGENTS = ("plan-reviewer", "map-reviewer", "data-scout", "spatial-analyst")


class Unavailable(Exception):
    """The model could not be reached.

    `transient` separates a busy API from a missing credential. The distinction
    matters more than it looks: an interview holds an officer's afternoon, and
    collapsing it because the service was briefly overloaded would lose work
    that is not the officer's fault. A transient failure is retried here and, if
    it persists, handed up as something to try again — the officer waits and
    resends the same turn rather than losing the conversation.
    """

    def __init__(self, message: str, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


def available() -> bool:
    return bool(
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or (Path.home() / ".config" / "anthropic").exists()
    )


# --- the transcript ------------------------------------------------------ #

@dataclass(frozen=True)
class Utterance:
    """One thing that was said. Officer utterances are what reasons are made of."""

    id: str
    speaker: str            # "officer" | "agent"
    text: str
    at: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%SZ"))


@dataclass
class ToolCall:
    """One tool the agent used, as the interface shows it beside the turn."""

    name: str
    input: dict[str, Any]
    ok: bool
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Turn:
    """What the agent produced before it stopped and waited for the officer."""

    said: str
    calls: list[ToolCall] = field(default_factory=list)
    reviewer: str = MODEL

    def to_dict(self) -> dict[str, Any]:
        return {"said": self.said, "calls": [c.to_dict() for c in self.calls],
                "by": self.reviewer}


# --- what the agent may do ----------------------------------------------- #

_ENUMS: dict[str, list[str]] = {
    k: [str(v) for v in vs] for k, vs in pricing.OPTIONS.items()
}
ALL_KEYS: list[str] = sorted({r.key for r in dec.REGISTER} | {r.key for r in dec.DEFERRED})

TOOLS: list[dict[str, Any]] = [
    {
        "name": "resolve_place",
        "description": (
            "Verify a country/district/ISO3 guess against the real boundary "
            "register before anything else runs. The officer typed a place in "
            "their own words, not necessarily the register's spelling — guess "
            "the country, the district name, and the ISO3 code yourself (you "
            "already know common ISO3 codes), then call this to check the guess "
            "against real data. It runs a real match, not a lookup table: on "
            "success it reports which administrative level matched and why "
            "(e.g. by the share of the register's own records the boundary "
            "contains), and updates the interview's scope to the confirmed "
            "values. On failure it returns the real reason nothing matched — "
            "put that to the officer and ask them to clarify or correct it; do "
            "not just guess again silently. Call this before scout."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "country": {"type": "string"},
                "adm2": {"type": "string",
                         "description": "the district, as best guessed"},
                "iso3": {"type": "string",
                        "description": "three-letter ISO 3166-1 alpha-3 code"},
            },
            "required": ["country", "adm2", "iso3"], "additionalProperties": False,
        },
    },
    {
        "name": "scout",
        "description": (
            "Report what the source register actually holds for this district: "
            "how many records, how many are serving, how old they are, and any "
            "anomaly the cleaning rules found. Coverage is deliberately not "
            "reported here, because what counts as covered depends on decisions "
            "that have not been made yet. Call this before putting any decision "
            "to the officer."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": [],
                         "additionalProperties": False},
    },
    {
        "name": "price_options",
        "description": (
            "Solve this district's data once per option for one decision, and "
            "return what each option would do. Needs the budget to have been "
            "recorded first, because every probe is solved at that budget. The "
            "figures come from real runs; do not quote a number this has not "
            "returned."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string", "enum": sorted(_ENUMS)}},
            "required": ["key"], "additionalProperties": False,
        },
    },
    {
        "name": "present_options",
        "description": (
            "Declare which decision you are asking about right now, so the "
            "officer's interface can show a card with that decision's priced "
            "options beside your message — keyed to exactly the id you call "
            "this with, never guessed by the interface. Call it once, right as "
            "you ask a value-laden question in your own words; it does not "
            "replace explaining the trade-off in prose, it gives the interface "
            "something concrete to render alongside it. Do not call it for a "
            "decision already recorded, or for one you are not asking about "
            "this turn — if you don't call it, the officer just sees your "
            "words, which is also fine."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string", "enum": ALL_KEYS}},
            "required": ["key"], "additionalProperties": False,
        },
    },
    {
        "name": "record_decision",
        "description": (
            "Record one decision against the officer's name. `reason_utterance_id` "
            "must be the id of something the OFFICER said; the harness copies that "
            "text into the record verbatim, and you cannot supply, edit or "
            "summarise it. If what they said is not yet a reason for this "
            "decision, ask them for one instead of calling this."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "enum": ALL_KEYS},
                "value": {"type": "string",
                          "description": "the chosen value, as written in the "
                                         "options; numbers as digits"},
                "reason_utterance_id": {"type": "string"},
            },
            "required": ["key", "value", "reason_utterance_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_assessment",
        "description": (
            "Run the assessment on the decisions recorded so far. If any decision "
            "is outstanding this returns the refusal and no plan; that refusal is "
            "the system working, not an error to route around.\n\n"
            "The result carries `results_json` and `figures_json`, paths (relative "
            "to the repository root) to give the plan-reviewer and map-reviewer "
            "subagents when you delegate to them — tell each exactly which file to "
            "read; do not paraphrase or transcribe the contents into the prompt "
            "yourself. `figure_review` in the result is null until the figures have "
            "been reviewed. Once map-reviewer returns its verdict (a JSON object "
            "with a `figures` list, each entry an `accept`/`revise`/`unreadable` "
            "verdict with findings), call run_assessment again passing that exact "
            "object as `figure_review` — this attaches it to the same run, marks "
            "the figures reviewed in the record, and is cheap (only the review "
            "and issue stages redo). Do not call run_assessment again without "
            "`figure_review` once a plan already exists; that would start a "
            "separate run rather than completing this one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "figure_review": {
                    "type": "object",
                    "description": (
                        "Only on a re-run after map-reviewer has reviewed this "
                        "run's figures. Must be exactly the JSON object "
                        "map-reviewer returned — do not construct or edit it "
                        "yourself."
                    ),
                    "properties": {
                        "reviewer": {"type": "string"},
                        "figures": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["figures"],
                },
            },
            "required": [], "additionalProperties": False,
        },
    },
    {
        "name": "apply_override",
        "description": (
            "Record one of the officer's four verbs against the current plan and "
            "re-solve, returning what it cost in people. As with a decision, the "
            "reason is an utterance id and is copied verbatim. An override is "
            "never blocked, only priced. REWEIGHT requires equity_weight (0-1, "
            "ask the officer for a number) and only applies when the run's "
            "objective is worst_case. RESCOPE here only changes the number of "
            "facilities (budget) — changing the service radius is not "
            "implemented and must not be offered."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "verb": {"type": "string",
                         "enum": ["VETO", "PIN", "REWEIGHT", "RESCOPE"]},
                "site_id": {"type": "string"},
                "reason_utterance_id": {"type": "string"},
                "veto_radius_m": {"type": "integer"},
                "equity_weight": {"type": "number"},
                "budget": {"type": "integer"},
            },
            "required": ["verb", "reason_utterance_id"],
            "additionalProperties": False,
        },
    },
]


class Refused(Exception):
    """A tool declining. The text goes back to the agent as the tool result."""


def _match_enum(key: str, value: str) -> str | None:
    """The register's own spelling of a value, if the model gave an equivalent.

    A radius is `1000.0` in the options and `1000` in anything a person would
    write. Refusing that costs a round trip and teaches the model nothing, so
    the numeric forms are reconciled here and the register's spelling wins.
    """
    allowed = _ENUMS[key]
    if value in allowed:
        return value
    try:
        want = float(value)
    except (TypeError, ValueError):
        return None
    for a in allowed:
        try:
            if float(a) == want:
                return a
        except ValueError:
            continue
    return None


# --- the SDK engine's plumbing -------------------------------------------- #
# Everything below wraps the same TOOLS schemas and Interview methods the
# legacy engine uses — no business logic is duplicated, only re-registered.

def _sdk_result_text(content: Any) -> str:
    """Flatten an SDK ToolResultBlock's content into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
    return "\n".join(parts)


def _sdk_tool_server(interview: "Interview"):
    """Re-host `TOOLS` + `Interview._dispatch` as SDK custom tools."""
    from claude_agent_sdk import create_sdk_mcp_server
    from claude_agent_sdk import tool as sdk_tool

    def make(spec: dict[str, Any]):
        name = spec["name"]

        async def handler(args: dict[str, Any], _name: str = name) -> dict[str, Any]:
            ok, out = await interview._dispatch(_name, args)
            return {"content": [{"type": "text", "text": _as_result(out)}],
                   **({"is_error": True} if not ok else {})}

        return sdk_tool(name, spec["description"], spec["input_schema"])(handler)

    return create_sdk_mcp_server(name="siting", version="1.0.0",
                                 tools=[make(s) for s in TOOLS])


# Tool names, beyond our own mcp__siting__* set and Agent/Task delegation
# (gated below by subagent_type), that this interview may use. ToolSearch is
# infrastructure — how the CLI resolves a deferred tool's schema before first
# calling it — not a capability grant. Everything else Claude Code's baseline
# roster offers (Write, Edit, Cron*, SendMessage, WebFetch, ...) is refused.
# Bash, Read and Grep are handled separately below: data-scout and
# spatial-analyst are declared with exactly that grant, so those three names
# are allowed through structurally, with Bash additionally checked line by
# line against `_BASH_DENY_PATTERNS`.
_EXTRA_ALLOWED_TOOLS = {"ToolSearch"}
_BASH_CAPABLE_TOOLS = {"Bash", "Read", "Grep"}

# Ported from harness/hooks/pre_tool_use.sh, which guards the CLI path but —
# confirmed by testing — is not consulted just because an SDK session's cwd
# points at this repo. Same patterns, same reasons, same substring-match
# looseness (a false-positive refusal is cheap; a false-negative isn't).
# `pre_tool_use.sh`'s fourth rule (destructive removal) is additionally
# covered by the SDK's own path-scoped sandbox for `rm`, confirmed by
# testing, but is repeated here rather than relied on implicitly.
_BASH_DENY_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("siting/", ">"), "the analysis code is read-only during a run"),
    (("handbooks/", ">"), "the source handbooks are read-only during a run"),
    (("sed -i", "siting/"), "the analysis code is read-only during a run"),
    (("sed -i", "handbooks/"), "the source handbooks are read-only during a run"),
    (("decisions/", ">"),
     "write a decisions file through record_decision, not a shell redirect, "
     "so its authorship is recorded"),
    (("echo", "decided_by"),
     "write a decisions file through record_decision, not a shell redirect, "
     "so its authorship is recorded"),
    (("git push",), "a run does not publish; delivery is a separate, deliberate act"),
    (("gh pr",), "a run does not publish; delivery is a separate, deliberate act"),
    (("gh release",), "a run does not publish; delivery is a separate, deliberate act"),
    (("curl -X POST",), "a run does not publish; delivery is a separate, deliberate act"),
    (("curl -d",), "a run does not publish; delivery is a separate, deliberate act"),
    (("rm -rf",), "removal is not part of a run; the cache is what makes it reproducible"),
    (("rm -r ",), "removal is not part of a run; the cache is what makes it reproducible"),
    (("rmdir",), "removal is not part of a run; the cache is what makes it reproducible"),
)


def _bash_denial(command: str) -> str | None:
    for needles, reason in _BASH_DENY_PATTERNS:
        if all(n in command for n in needles):
            return reason
    return None


async def _gate_tools(input_data: dict[str, Any], tool_use_id: str | None,
                      context: Any) -> dict[str, Any]:
    """A PreToolUse hook: the one place that decides what this conversation
    may do beyond its own seven tools plus data-scout/spatial-analyst's
    declared Bash/Read/Grep. Default-deny, not default-allow — per this
    module's own rule that the guarantee is in the shape of the tool, not in
    what the prompt asks for, an officer-facing interview should never be one
    confused turn away from a generic capability (Write, arbitrary
    delegation, an unreviewed shell command, ...) nobody reviewed for this
    setting.

    This is a hook, not `can_use_tool`: testing found `can_use_tool` is
    simply never consulted for an Agent/Task delegation call, regardless of
    `allowed_tools` — the SDK's own warning about `allowed_tools` shadowing
    the callback turned out not to be the whole story. A `PreToolUse` hook
    fires for every tool call including delegation and a subagent's own
    tool calls, confirmed by testing, so it is the one mechanism this can
    actually rely on for both.

    `agents=` on ClaudeAgentOptions only adds or overrides entries by name;
    it does not restrict the SDK's own auto-discovery of `.claude/agents/*.md`
    or Claude Code's own generic built-ins (Explore, general-purpose, Plan,
    ...) — also confirmed by testing.
    """
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input") or {}

    if tool_name in ("Agent", "Task"):
        wanted = tool_input.get("subagent_type")
        if wanted in SDK_AGENTS:
            return {}
        return {"decision": "block",
               "reason": f"{wanted!r} is not one of the subagents this "
                         f"interview may delegate to ({', '.join(SDK_AGENTS)})."}
    if tool_name == "Bash":
        reason = _bash_denial(tool_input.get("command", ""))
        if reason:
            return {"decision": "block", "reason": reason}
        return {}
    if tool_name in _BASH_CAPABLE_TOOLS - {"Bash"}:  # Read, Grep — always fine
        return {}
    if tool_name.startswith("mcp__siting__") or tool_name in _EXTRA_ALLOWED_TOOLS:
        return {}
    return {"decision": "block",
           "reason": f"{tool_name!r} is not a tool this interview may use."}


class Interview:
    """One agent-run interview: its transcript, its decisions, its runs."""

    def __init__(self, base: dict[str, Any], session_dir: Path, officer: str,
                 transport: Callable[..., Awaitable[Any]] | None = None) -> None:
        self.base = base
        self.dir = Path(session_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.officer = officer
        self.utterances: list[Utterance] = []
        self.recorded: dict[str, dict[str, Any]] = {}
        self.overrides: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.last_run: dict[str, Any] | None = None
        self._transport = transport
        # The SDK engine's own conversation id — it owns history itself
        # (`resume=`), unlike the legacy engine, which replays `self.messages`
        # into a fresh client every turn. `None` until the first SDK turn.
        self.sdk_session_id: str | None = None

    # --- transcript ------------------------------------------------------ #

    def say(self, text: str, speaker: str = "officer") -> Utterance:
        u = Utterance(id=f"u{len(self.utterances) + 1}", speaker=speaker,
                      text=text.strip())
        self.utterances.append(u)
        return u

    def _officer_words(self, utterance_id: str) -> str:
        """The one place a reason comes from. Never an argument the model wrote."""
        for u in self.utterances:
            if u.id == utterance_id:
                if u.speaker != "officer":
                    raise Refused(
                        f"{utterance_id} is something you said, not the officer. A "
                        "reason has to be the officer's own words.")
                if not u.text.strip():
                    raise Refused(f"{utterance_id} is empty.")
                return u.text
        raise Refused(
            f"there is no utterance {utterance_id}. Officer turns so far: "
            + ", ".join(u.id for u in self.utterances if u.speaker == "officer"))

    def transcript(self) -> list[dict[str, Any]]:
        return [asdict(u) for u in self.utterances]

    # --- tools ----------------------------------------------------------- #

    def _require_resolved(self) -> None:
        """The guarantee lives here, not in the prompt.

        A model can be talked out of an instruction; it cannot be talked into a
        capability it was not given. Nothing that depends on a confirmed place
        (scouting the register, pricing an option, running the assessment) can
        proceed on a guess that was never checked.
        """
        if not self.base.get("iso3"):
            raise Refused("the place has not been resolved yet. Call "
                          "resolve_place with your best guess of country, adm2 "
                          "and iso3 first.")

    async def _present_options(self, key: str) -> dict[str, Any]:
        """Declare, for the interface, which decision this turn is asking about.

        The interview used to let the web layer guess this from a fixed
        question order, and the guess and the agent's actual question could
        disagree — the officer would see a card for one decision while the
        agent's own words asked about another. This closes that gap by
        letting the agent state the key itself, the one thing it always
        knows and the interface never can.
        """
        self._require_resolved()
        if key in self.recorded:
            raise Refused(f"{key} is already recorded; there is nothing left to "
                          "present. If you meant to ask about a different "
                          "decision, call this again with that key.")
        spec = next((r for r in (*dec.REGISTER, *dec.DEFERRED) if r.key == key), None)
        if spec is None:
            raise Refused(f"{key!r} is not a decision this register declares")
        return {"presented": key, "question": spec.question,
               "note": "the officer's interface now shows a card for this "
                       "decision, keyed to what you just called"}

    async def _resolve_place(self, country: str, adm2: str, iso3: str) -> dict[str, Any]:
        """Check a guess against the real boundary register, not a lookup table.

        Runs the same subprocess `siting.cli` runs everything through
        (`--mode auto --budget 1`, exactly what `scout` does) and reads back
        `results.json["scope"]["boundary_match"]` — the real account of which
        administrative level matched and why, produced by
        `siting.sources.gadm.district`. On a match this updates `self.base` to
        the confirmed values; on a miss it raises with the CLI's own refusal
        text so the agent can put that to the officer rather than guess again.
        """
        domain = self.base.get("domain", "water")
        spec = RunSpec(country=country, adm2=adm2, iso3=iso3.upper(), domain=domain,
                       out_dir=self.dir / "resolving", mode="auto", budget=1,
                       fmt="bundle")
        tail: list[str] = []
        done = None
        async for ev in runner.stream(spec):
            if ev.kind in ("log", "detail") and ev.text.strip():
                # Drop bare traceback frames ('File "...", line N, in ...') and
                # keep the lines a person would actually read — the exception
                # message itself, and any refusal text the CLI prints on
                # purpose. Real text either way; this only drops noise.
                if not re.match(r'^\s*File "', ev.text):
                    tail.append(ev.text.strip())
            if ev.kind == "done":
                done = ev
        run_dir = done.data.get("run_dir") if done and done.data else None
        try:
            if not run_dir:
                raise FileNotFoundError
            doc = runner.results(Path(run_dir))
        except (FileNotFoundError, json.JSONDecodeError):
            # A directory can exist with no results.json in it: the CLI makes
            # its output directory before it can fail matching the district
            # against the boundary register, so "a directory appeared" is not
            # proof of a match. The real reason is in the log tail.
            raise Refused("\n".join(tail[-6:]) or
                          f"no match for {adm2!r} in {country!r} ({iso3.upper()})")
        finally:
            # A probe at budget=1 is scaffolding, not part of the account: what
            # matters is folded into `self.base` below, and the tool result
            # itself is what the harness keeps in the transcript. Nothing ever
            # reads this directory again, matched or not.
            if run_dir:
                shutil.rmtree(run_dir, ignore_errors=True)
        s = doc["scope"]
        self.base["country"], self.base["adm2"], self.base["iso3"] = (
            s["country"], s["adm2"], s["iso3"])
        self.base["domain"] = domain
        return {
            "resolved": True,
            "country": s["country"], "adm2": s["adm2"], "iso3": s["iso3"],
            "boundary": s.get("boundary"),
            "boundary_match": s.get("boundary_match"),
        }

    async def _scout(self) -> dict[str, Any]:
        """Register facts only: nothing here depends on a decision not yet made."""
        self._require_resolved()
        spec = RunSpec(country=self.base["country"], adm2=self.base["adm2"],
                       iso3=self.base["iso3"], domain=self.base.get("domain", "water"),
                       out_dir=self.dir / "scouting", mode="auto", budget=1,
                       fmt="bundle")
        done = None
        async for ev in runner.stream(spec):
            if ev.kind == "done":
                done = ev
        if not (done and done.data and done.data["run_dir"]):
            raise Refused("the register could not be read for this district")
        run_dir = Path(done.data["run_dir"])
        try:
            doc = runner.results(run_dir)
        finally:
            # Same reasoning as `_resolve_place`: nothing reads this directory
            # again. The figures below are what the harness keeps.
            shutil.rmtree(run_dir, ignore_errors=True)
        s = doc["scope"]
        return {
            "district": f"{s['adm2']}, {s['country']}",
            "records_total": s["points_total"],
            "records_serving": s["points_working"],
            "records_not_serving": s["points_broken"],
            "median_record_age_years": s["median_record_age_years"],
            "population": doc["baseline"]["population"],
            "boundary": s.get("boundary"),
            "boundary_match": (s.get("boundary_match") or {}).get("how"),
            "anomalies": [{"kind": a["kind"], "observed": a["observed"]}
                          for a in doc.get("anomalies", [])],
            "note": ("Coverage is not reported here. What counts as covered depends "
                     "on the service radius and the coverage basis, which are the "
                     "officer's decisions and have not been made."),
        }

    async def _price_options(self, key: str) -> dict[str, Any]:
        self._require_resolved()
        if "budget" not in self.recorded:
            raise Refused(
                "budget has not been recorded. Every probe is solved at the "
                "budget the programme funds, so that has to be settled first. "
                "It comes from a capital programme; there is nothing in the data "
                "to infer it from.")
        tables = await pricing.price(self.base, int(self.recorded["budget"]["value"]),
                                     keys=(key,))
        t = tables[key]
        return {
            "key": key,
            "question": t.question,
            "why_this_is_not_a_default": t.why_not_a_default,
            "options": [
                {"value": p.value, "label": p.label,
                 "already_served": p.covered_today,
                 "already_served_share": round(p.covered_share, 4),
                 "programme_would_add": p.newly_covered,
                 "evidence": p.evidence}
                for p in t.priced
            ],
            "caveat": t.caveat,
            "warning": t.degenerate,
        }

    async def _record_decision(self, key: str, value: str,
                               reason_utterance_id: str) -> dict[str, Any]:
        self._require_resolved()
        if key in self.recorded:
            raise Refused(f"{key} is already recorded; a decision has one answer. "
                          "Ask the officer to change it explicitly if they mean to.")
        if key in _ENUMS:
            matched = _match_enum(key, value)
            if matched is None:
                raise Refused(f"{value!r} is not one of {_ENUMS[key]} for {key}.")
            value = matched

        reason = self._officer_words(reason_utterance_id)   # verbatim, or Refused
        answer = emit.Answer(key=key, value=value, decided_by=self.officer,
                             reason=reason)
        try:
            written = emit.write_decisions(
                self.dir / "decisions.yaml",
                [emit.Answer(**{**asdict(a)}) for a in
                 [*(emit.Answer(**v["answer"]) for v in self.recorded.values()), answer]],
                mode="manual", district=self.base["adm2"])
        except emit.Refused as exc:
            raise Refused(str(exc)) from exc

        self.recorded[key] = {"answer": asdict(answer), "value": value,
                              "reason_from": reason_utterance_id}
        return {
            "recorded": key, "value": value,
            "reason_recorded_verbatim": reason,
            "reason_taken_from": reason_utterance_id,
            "outstanding": list(written.outstanding),
            "progress": questions.progress(self.recorded)["statement"],
        }

    async def _run_assessment(self, figure_review: dict[str, Any] | None = None
                              ) -> dict[str, Any]:
        self._require_resolved()

        resume_id: str | None = None
        review_path: Path | None = None
        if figure_review is not None:
            if not (self.last_run and self.last_run.get("run_dir")):
                raise Refused(
                    "there is no prior run to attach a figure review to. Call "
                    "run_assessment once first, without figure_review, then "
                    "delegate to map-reviewer, then call this again with its "
                    "verdict.")
            if "figures" not in figure_review:
                raise Refused("figure_review must carry a 'figures' list — pass "
                              "map-reviewer's result exactly as it returned it.")
            # The same validation siting/report/figure_brief.py:load_verdict
            # does, run here first rather than only there: that check runs
            # deep inside the CLI subprocess, after a full paid run, and its
            # failure ("figure review rejected: ...") lands in the run log —
            # a place this method does not surface back to the model on the
            # success path. A live run caught this: a verdict keyed "figure"
            # instead of "file" was silently rejected downstream and nothing
            # ever told the model why figure_review stayed null.
            for i, f in enumerate(figure_review["figures"]):
                missing = [k for k in ("file", "verdict", "findings") if k not in f]
                if missing:
                    raise Refused(
                        f"figure_review.figures[{i}] is missing "
                        f"{', '.join(repr(m) for m in missing)}. Each entry "
                        "must be keyed exactly 'file' (not 'figure' or "
                        "anything else), 'verdict' and 'findings' — this has "
                        "to be map-reviewer's result passed through "
                        "unedited, not reconstructed from memory.")
                if f["verdict"] not in ("accept", "revise", "unreadable"):
                    raise Refused(
                        f"figure_review.figures[{i}] ({f['file']!r}) has "
                        f"verdict {f['verdict']!r}; must be 'accept', "
                        "'revise' or 'unreadable'.")
            prior_dir = Path(self.last_run["run_dir"])
            resume_id = prior_dir.name
            review_path = prior_dir / "figure_review.json"
            review_path.write_text(
                json.dumps(figure_review, ensure_ascii=False, indent=2),
                encoding="utf-8")

        spec = RunSpec(country=self.base["country"], adm2=self.base["adm2"],
                       iso3=self.base["iso3"], domain=self.base.get("domain", "water"),
                       out_dir=self.dir / "runs",
                       decisions=self.dir / "decisions.yaml",
                       overrides=(self.dir / "overrides.yaml"
                                  if self.overrides else None),
                       mode="manual", fmt="both",
                       resume=resume_id, figure_review=review_path)
        lines, done = [], None
        async for ev in runner.stream(spec, transcript=self.dir / "run.log"):
            lines.append(ev.text)
            if ev.kind == "done":
                done = ev
        assert done and done.data

        outcome = done.data["outcome"]
        if outcome == "decisions_outstanding":
            return {"issued": False, "outcome": outcome,
                    "the_run_refused_to_start": "\n".join(lines[:-1])[:4000],
                    "what_to_do": ("Put the outstanding decisions to the officer. "
                                   "Do not answer them yourself.")}
        run_dir_out = done.data["run_dir"]
        if not run_dir_out:
            return {"issued": False, "outcome": outcome,
                    "log_tail": "\n".join(lines[-12:])}

        run_dir = Path(run_dir_out)
        doc = runner.results(run_dir)
        self.last_run = {"run_dir": run_dir_out, "outcome": outcome}
        rel = run_dir.relative_to(REPO)
        return {
            "issued": done.data["issued"], "outcome": outcome,
            "population": doc["baseline"]["population"],
            "covered_today": doc["baseline"]["covered"],
            "sites": [{"id": s["id"], "rank": s["rank"], "lat": s["lat"],
                       "lon": s["lon"], "newly_covered": s["newly_covered"],
                       "nearest_working_m": s["nearest_working_m"],
                       "rationale": s["rationale"]} for s in doc["plan"]["sites"]],
            "newly_covered": doc["plan"]["newly_covered"],
            "checks": [{"check": c["check"], "level": c["level"], "detail": c["detail"]}
                       for c in doc["evaluation"]],
            "review": {"weighted": doc["review"]["weighted"],
                       "floor": doc["review"]["accept_at"],
                       "decision": doc["review"]["decision"],
                       "scores": doc["review"]["scores"]},
            "authorship": doc["authorship"]["statement"],
            "results_json": str(rel / "results.json"),
            "figures_json": str(rel / "figures.json"),
            "figure_review": (doc.get("figure_review") or {}).get("summary"),
            # A safety net for exactly the failure this method's own
            # figure_review validation above was added to catch: something
            # the CLI itself refused deep inside the run, which would
            # otherwise show up nowhere except a log file this method never
            # reads back. Anything printed on rejection or refusal surfaces
            # here even though the run overall issued.
            "log_notes": [l for l in lines
                         if "rejected" in l.lower() or "refused" in l.lower()] or None,
        }

    async def _apply_override(self, verb: str, reason_utterance_id: str,
                              site_id: str | None = None,
                              veto_radius_m: int | None = None,
                              equity_weight: float | None = None,
                              budget: int | None = None) -> dict[str, Any]:
        if self.last_run is None:
            raise Refused("there is no plan yet to override. Run the assessment first.")
        before = runner.results(Path(self.last_run["run_dir"]))["plan"]

        item: dict[str, Any] = {"verb": verb, "actor": self.officer,
                                "reason": self._officer_words(reason_utterance_id)}
        if site_id:
            item["site"] = site_id
        if veto_radius_m:
            item["veto_radius_m"] = float(veto_radius_m)
        if equity_weight is not None:
            item["equity_weight"] = float(equity_weight)
        if budget is not None:
            item["budget"] = int(budget)
        try:
            emit.write_overrides(self.dir / "overrides.yaml",
                                 [*self.overrides, item], district=self.base["adm2"])
        except emit.Refused as exc:
            raise Refused(str(exc)) from exc
        self.overrides.append(item)

        after = await self._run_assessment()
        if not after.get("issued"):
            # Either the re-solve itself failed (REWEIGHT on a non-worst_case
            # run raises inside siting.overrides, for instance) or the
            # independent checks rejected the post-override plan. Either way
            # there is no priced outcome to report, so undo the write rather
            # than let a non-outcome masquerade as a recorded, priced one.
            self.overrides.pop()
            emit.write_overrides(self.dir / "overrides.yaml", self.overrides,
                                 district=self.base["adm2"])
            detail = (after.get("log_tail") or after.get("the_run_refused_to_start")
                      or "the re-run did not produce an issuable plan")
            raise Refused(f"this override was not recorded — the re-run after "
                          f"it did not produce a priced, issuable plan: {detail}")

        cost = before["covered"] - (after.get("covered_today", 0) +
                                    after.get("newly_covered", 0))
        return {
            "override": {k: v for k, v in item.items() if k != "reason"},
            "reason_recorded_verbatim": item["reason"],
            "people_forgone": max(0, cost),
            "plan_after": after.get("sites", []),
            "note": "The override was applied. It was priced, not blocked.",
        }

    async def _dispatch(self, name: str, args: dict[str, Any]) -> tuple[bool, Any]:
        handlers = {
            "resolve_place": self._resolve_place,
            "present_options": self._present_options,
            "scout": self._scout,
            "price_options": self._price_options,
            "record_decision": self._record_decision,
            "run_assessment": self._run_assessment,
            "apply_override": self._apply_override,
        }
        if name not in handlers:
            return False, {"refused": f"there is no tool called {name}"}
        try:
            return True, await handlers[name](**args)
        except Refused as exc:
            return False, {"refused": str(exc)}
        except Exception as exc:                        # a tool failing is data
            return False, {"refused": f"{type(exc).__name__}: {exc}"}

    # --- the loop -------------------------------------------------------- #

    async def _call_model(self, system: list[dict]) -> Any:
        if self._transport is not None:
            return await self._transport(system=system, messages=self.messages,
                                         tools=TOOLS)
        if not available():
            raise Unavailable("no ANTHROPIC_API_KEY and no stored profile")

        import anthropic
        client = anthropic.AsyncAnthropic(max_retries=2)
        last: Exception | None = None
        for attempt in range(MODEL_RETRIES):
            try:
                return await client.messages.create(
                    model=MODEL, max_tokens=MAX_TOKENS,
                    system=system, messages=self.messages, tools=TOOLS,
                    thinking={"type": "adaptive"},
                )
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
                status = getattr(exc, "status_code", None)
                transient = (isinstance(exc, anthropic.APIConnectionError)
                             or (status is not None and (status == 429 or status >= 500)))
                if not transient:
                    raise Unavailable(f"{type(exc).__name__}: {exc}") from exc
                last = exc
                if attempt + 1 < MODEL_RETRIES:
                    await asyncio.sleep(RETRY_BACKOFF[attempt]
                                        + random.uniform(0, 1.5))
            except Exception as exc:
                raise Unavailable(f"{type(exc).__name__}: {exc}") from exc

        raise Unavailable(
            f"the model was busy through {MODEL_RETRIES} attempts "
            f"({type(last).__name__}). The interview is intact: nothing was lost, "
            "and the same turn can be sent again.", transient=True)

    async def advance_events(self, said: str | None = None):
        """Run the agent until it is waiting on the officer, yielding as it goes.

        Which engine does this is a rollback switch (`ENGINE`), not a
        conversation-shaped choice — both yield the identical `{"kind": ...}`
        event contract, so nothing downstream needs to know which one ran.
        """
        engine = (self._advance_events_sdk if ENGINE == "sdk"
                 else self._advance_events_legacy)
        async for ev in engine(said):
            yield ev

    async def _advance_events_legacy(self, said: str | None = None):
        """Run the agent until it is waiting on the officer, yielding as it goes.

        A turn can take half a minute, most of it a tool solving this district's
        data for real. A spinner over that would hide the one thing worth
        showing, so each tool call is announced when it starts and again when it
        returns, and the text arrives last.
        """
        if said is not None:
            u = self.say(said, speaker="officer")
            self.messages.append({"role": "user", "content": f"[{u.id}] {u.text}"})
            yield {"kind": "heard", "utterance": u.id}
        elif not self.messages:
            if self.base.get("iso3"):
                self.messages.append({"role": "user", "content": (
                    f"Begin the assessment for {self.base['adm2']}, "
                    f"{self.base['country']} ({self.base['iso3']}), domain "
                    f"{self.base.get('domain', 'water')}. The officer is: "
                    f"{self.officer}.")})
            else:
                self.messages.append({"role": "user", "content": (
                    f"The officer typed this place: {self.base.get('where', '')!r}. "
                    f"Domain: {self.base.get('domain', 'water')}. The officer is: "
                    f"{self.officer}. Nothing has been fetched yet — resolve the "
                    "place with resolve_place before doing anything else.")})

        # Only officer turns can become a reason, so only those need ids the
        # model can cite. The conversation itself is already in `messages`;
        # restating it here would double the cost of every turn.
        citable = [u for u in self.utterances if u.speaker == "officer"]
        system = prompts.system_blocks(
            "Utterance ids you may cite as a reason (officer turns only):\n"
            + "\n".join(f"  [{u.id}] {u.text}" for u in citable)
            if citable else "")

        calls: list[ToolCall] = []
        for _ in range(MAX_TOOL_TURNS):
            yield {"kind": "thinking"}
            resp = await self._call_model(system)
            self.messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                text = "".join(b.text for b in resp.content
                               if getattr(b, "type", "") == "text").strip()
                if text:
                    self.say(text, speaker="agent")
                yield {"kind": "said", "text": text}
                yield {"kind": "done", "turn": Turn(said=text, calls=calls).to_dict()}
                return

            results = []
            for block in resp.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                args = dict(block.input)
                yield {"kind": "tool", "name": block.name, "input": args}
                ok, out = await self._dispatch(block.name, args)
                call = ToolCall(name=block.name, input=args, ok=ok,
                                summary=(out.get("refused") if not ok
                                         else _summarise(block.name, out)))
                calls.append(call)
                yield {"kind": "tool_done", **call.to_dict()}
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": _as_result(out),
                                **({"is_error": True} if not ok else {})})
            self.messages.append({"role": "user", "content": results})

        stuck = ("(the agent used its tool budget for this turn without coming "
                 "back with anything to say)")
        yield {"kind": "said", "text": stuck}
        yield {"kind": "done", "turn": Turn(said=stuck, calls=calls).to_dict()}

    async def _advance_events_sdk(self, said: str | None = None):
        """Same contract as `_advance_events_legacy`, run on the Agent SDK.

        The SDK owns the tool-call loop and the conversation history itself
        (`resume=self.sdk_session_id`, not `self.messages` — there is no way
        to hand it a pre-built message array the way the raw Messages API
        takes one). This method's whole job is translating its message stream
        into the same events the legacy engine yields, so the frontend and
        `session.py` don't need to know which engine is running.
        """
        from claude_agent_sdk import (
            AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher,
            ResultMessage, SystemMessage, TextBlock, ToolResultBlock, ToolUseBlock,
            UserMessage,
        )

        if said is not None:
            u = self.say(said, speaker="officer")
            query_text = f"[{u.id}] {u.text}"
            yield {"kind": "heard", "utterance": u.id}
        elif self.sdk_session_id is None:
            if self.base.get("iso3"):
                query_text = (
                    f"Begin the assessment for {self.base['adm2']}, "
                    f"{self.base['country']} ({self.base['iso3']}), domain "
                    f"{self.base.get('domain', 'water')}. The officer is: "
                    f"{self.officer}.")
            else:
                query_text = (
                    f"The officer typed this place: {self.base.get('where', '')!r}. "
                    f"Domain: {self.base.get('domain', 'water')}. The officer is: "
                    f"{self.officer}. Nothing has been fetched yet — resolve the "
                    "place with resolve_place before doing anything else.")
        else:
            # No new officer input on an already-open session. The legacy
            # engine can re-ask the model against unchanged history because it
            # replays the whole array; the SDK has no such no-op query.
            query_text = "Continue."

        citable = [u for u in self.utterances if u.speaker == "officer"]
        extra = ("Utterance ids you may cite as a reason (officer turns only):\n"
                 + "\n".join(f"  [{u.id}] {u.text}" for u in citable)) if citable else ""

        # Retried at this level, not inside a single client call, because a
        # transient failure here (rate_limit/server_error/a dropped
        # connection) can land mid-stream, after real work already happened —
        # unlike the legacy engine's retry, which only ever wraps a single
        # request before any tool has run. A retry resumes the same SDK
        # session (once `self.sdk_session_id` exists) with a plain "Continue."
        # rather than resubmitting the officer's own message a second time.
        calls: list[ToolCall] = []
        said_text = ""
        last_error: Unavailable | None = None
        succeeded = False

        for attempt in range(MODEL_RETRIES):
            resume_id = self.sdk_session_id
            this_query = (query_text if attempt == 0 or resume_id is None
                         else "Continue.")

            options = ClaudeAgentOptions(
                cwd=str(REPO),
                setting_sources=["project"],
                mcp_servers={"siting": _sdk_tool_server(self)},
                agents={name: prompts.agent_definition(name) for name in SDK_AGENTS},
                allowed_tools=[f"mcp__siting__{t['name']}" for t in TOOLS],
                # `_gate_tools` is a PreToolUse hook, not `can_use_tool` —
                # testing found `can_use_tool` is simply never consulted for
                # an Agent/Task delegation call. `matcher=None` fires it for
                # every tool, which is what a default-deny gate needs: it
                # decides what's allowed, not just what the model happened to
                # ask permission for.
                hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[_gate_tools])]},
                system_prompt=prompts.system_text(extra),
                resume=resume_id,
                model=MODEL,
                max_turns=MAX_TOOL_TURNS,
            )

            pending: dict[str, tuple[str, dict[str, Any], bool]] = {}
            calls = []
            said_text = ""
            attempt_error: Unavailable | None = None

            try:
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(this_query)
                    yield {"kind": "thinking"}
                    async for message in client.receive_response():
                        if isinstance(message, SystemMessage):
                            if message.subtype == "init":
                                sid = message.data.get("session_id")
                                if sid:
                                    self.sdk_session_id = sid
                            continue

                        if isinstance(message, AssistantMessage):
                            if message.error:
                                transient = message.error in ("rate_limit",
                                                              "server_error")
                                attempt_error = Unavailable(
                                    f"sdk: {message.error}", transient=transient)
                                break
                            has_tool = False
                            for block in message.content:
                                if isinstance(block, ToolUseBlock):
                                    has_tool = True
                                    is_delegation = block.name in ("Agent", "Task")
                                    name = (block.input.get("subagent_type", "subagent")
                                           if is_delegation
                                           else block.name.removeprefix("mcp__siting__"))
                                    pending[block.id] = (name, dict(block.input),
                                                         is_delegation)
                                    yield {"kind": "tool", "name": name,
                                          "input": dict(block.input)}
                            if not has_tool:
                                said_text = "".join(
                                    b.text for b in message.content
                                    if isinstance(b, TextBlock)).strip()
                            continue

                        if isinstance(message, UserMessage):
                            content = (message.content
                                      if isinstance(message.content, list) else [])
                            for block in content:
                                if not isinstance(block, ToolResultBlock):
                                    continue
                                name, args, is_delegation = pending.pop(
                                    block.tool_use_id, ("unknown", {}, False))
                                ok = not bool(block.is_error)
                                text = _sdk_result_text(block.content)
                                if is_delegation:
                                    # A delegation's result — the subagent's
                                    # own answer, or this turn's PreToolUse
                                    # denial reason if it was refused — is
                                    # prose, not JSON, and on success carries
                                    # a trailing internal metadata block
                                    # (agentId/usage) after the real answer
                                    # that `_sdk_result_text` already joined
                                    # in; only the first line is worth
                                    # showing beside the turn either way.
                                    first = (text.strip().splitlines()[0]
                                            if text.strip() else "")
                                    summary = first[:160] or "no result"
                                else:
                                    try:
                                        out = json.loads(text)
                                    except (TypeError, ValueError):
                                        out = {"raw": text}
                                    summary = (out.get("refused") if not ok
                                              else _summarise(name, out))
                                call = ToolCall(name=name, input=args, ok=ok,
                                                summary=summary or "")
                                calls.append(call)
                                yield {"kind": "tool_done", **call.to_dict()}
                            if content:
                                yield {"kind": "thinking"}
                            continue

                        if (isinstance(message, ResultMessage) and message.is_error
                                and not said_text):
                            said_text = message.result or (
                                "(the model reported an error and produced "
                                f"nothing to say: {message.subtype})")
            except Exception as exc:
                # A raw connection/process failure rather than an
                # API-reported error classification — no more specific
                # signal is available, so treat it the way the legacy
                # engine treats APIConnectionError: transient.
                attempt_error = (exc if isinstance(exc, Unavailable) else
                                 Unavailable(f"{type(exc).__name__}: {exc}",
                                            transient=True))

            if attempt_error is None:
                succeeded = True
                break
            if not attempt_error.transient:
                raise attempt_error
            last_error = attempt_error
            if attempt + 1 < MODEL_RETRIES:
                yield {"kind": "thinking"}
                await asyncio.sleep(RETRY_BACKOFF[attempt] + random.uniform(0, 1.5))

        if not succeeded:
            raise Unavailable(
                f"the model was busy through {MODEL_RETRIES} attempts "
                f"({type(last_error).__name__ if last_error else '?'}). The "
                "interview is intact: nothing was lost, and the same turn "
                "can be sent again.", transient=True)

        if not said_text:
            said_text = ("(the agent used its tool budget for this turn without "
                        "coming back with anything to say)")
        self.say(said_text, speaker="agent")
        yield {"kind": "said", "text": said_text}
        yield {"kind": "done", "turn": Turn(said=said_text, calls=calls).to_dict()}

    async def advance(self, said: str | None = None) -> Turn:
        """The whole turn at once, for callers that do not stream."""
        last = None
        async for ev in self.advance_events(said):
            if ev["kind"] == "done":
                last = ev["turn"]
        return Turn(said=last["said"],
                    calls=[ToolCall(**c) for c in last["calls"]])


RESULT_LIMIT = 20000


def _as_result(out: dict[str, Any]) -> str:
    """A tool result the model can actually parse.

    A long result truncated mid-object is worse than a short one: the model
    reads half a number and has no way to know it. Drop whole fields, largest
    first, and say what was dropped.
    """
    text = json.dumps(out, ensure_ascii=False)
    if len(text) <= RESULT_LIMIT:
        return text
    kept = dict(out)
    dropped = []
    for key in sorted(kept, key=lambda k: len(json.dumps(kept[k], ensure_ascii=False)),
                      reverse=True):
        if len(json.dumps(kept, ensure_ascii=False)) <= RESULT_LIMIT - 200:
            break
        if key in ("refused", "outcome", "issued"):
            continue
        kept.pop(key)
        dropped.append(key)
    kept["_omitted"] = (f"{', '.join(dropped)} were too long to return. Ask for "
                        "them one at a time if you need them.")
    return json.dumps(kept, ensure_ascii=False)


def _summarise(name: str, out: dict[str, Any]) -> str:
    """One line for the panel beside the turn. Never a figure the page relies on."""
    if name == "resolve_place":
        m = out.get("boundary_match") or {}
        return (f"{out['adm2']}, {out['country']} ({out['iso3']}) — matched "
                f"at level {m.get('level', '?')}, {m.get('how', '?')}")
    if name == "present_options":
        return f"showing the officer a card for {out['presented']}"
    if name == "scout":
        return (f"{out['records_total']:,} records, {out['records_serving']:,} serving, "
                f"median {out['median_record_age_years']} years old")
    if name == "price_options":
        return f"{len(out['options'])} options priced on real runs"
    if name == "record_decision":
        return f"{out['recorded']} = {out['value']}; {out['progress']}"
    if name == "run_assessment":
        if not out.get("issued"):
            return f"not issued: {out['outcome']}"
        return f"{len(out['sites'])} sites, {out['newly_covered']:,} newly covered"
    if name == "apply_override":
        return f"applied; {out['people_forgone']:,} people forgone"
    return "done"
