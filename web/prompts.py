"""Loads the skills. Contains no instructions of its own.

    skills/siting-run/SKILL.md        how an assessment is conducted
    skills/spatial-siting/SKILL.md    the analysis framework and its guardrails
    .claude/agents/data-scout.md      what the scout reports, and does not judge
    .claude/agents/spatial-analyst.md what the analyst prices, and does not decide
    .claude/agents/plan-reviewer.md   what the reviewer may and may not see

These are read at run time and become the agent's system prompt. **Nothing about
how the interview is conducted is written in the web layer.** Editing a skill
changes the demonstration's behaviour without touching this directory, which is
the claim the whole arrangement rests on and the one a reviewer will test.

The agents' tool grants are read from the same frontmatter and shown in the
interface. That a reviewer can only read, and cannot amend the plan it is
judging, is a line of configuration in the repository; on screen it can be
something a reader remembers.

The loaded text is a stable prefix and carries `cache_control`, so it is not
re-billed on every turn of the loop.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SKILLS = {
    "run": REPO / "skills" / "siting-run" / "SKILL.md",
    "spatial": REPO / "skills" / "spatial-siting" / "SKILL.md",
}
AGENTS = REPO / ".claude" / "agents"


@dataclass(frozen=True)
class AgentCard:
    """One subagent as the repository declares it, for showing in the interface."""

    name: str
    description: str
    tools: tuple[str, ...]

    @property
    def read_only(self) -> bool:
        return not ({"Write", "Edit", "NotebookEdit"} & set(self.tools))


def _read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(
            f"{path.relative_to(REPO)} is missing. The web layer has no copy of it "
            "and deliberately does not: the skill is the source of the agent's "
            "instructions, not a mirror of them."
        )
    return path.read_text(encoding="utf-8")


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        return {}, text
    fields: dict[str, str] = {}
    key = None
    for line in m.group(1).splitlines():
        if re.match(r"^\S.*?:", line):
            key, _, val = line.partition(":")
            key = key.strip()
            fields[key] = val.strip()
        elif key and line.strip():
            fields[key] += " " + line.strip()
    return fields, m.group(2)


@lru_cache(maxsize=None)
def skill(name: str) -> str:
    """One skill's body, without its frontmatter."""
    _, body = _frontmatter(_read(SKILLS[name]))
    return body.strip()


@lru_cache(maxsize=None)
def agents() -> tuple[AgentCard, ...]:
    """Every subagent the repository declares, with its tool grant."""
    out = []
    for p in sorted(AGENTS.glob("*.md")):
        fields, _ = _frontmatter(_read(p))
        tools = tuple(t.strip() for t in fields.get("tools", "").split(",") if t.strip())
        out.append(AgentCard(name=fields.get("name", p.stem),
                             description=fields.get("description", "").strip(),
                             tools=tools))
    return tuple(out)


# The only sentences this module contributes. They say where the agent is, not
# what it should decide: everything about conducting an assessment comes from
# the skill above. Kept short on purpose — a long preamble here would be the
# web layer quietly writing its own skill.
FRAME = """\
You are conducting this assessment through a web interface, in conversation with
one district officer. The skill above is your instruction; follow it.

Three things about this setting that the skill does not describe.

The officer typed the place in their own words — not necessarily the register's
spelling, and not yet checked against anything. If the interview opens with a
place that has not been resolved (you will be told so explicitly), your first
move is `resolve_place`: guess the country, the district, and the ISO3 code
yourself, and let the tool check that guess against the real boundary register
before you do anything else with it. Report what matched and how in your own
words — which administrative level, and why that one rather than another with
the same name, exactly as the tool's result states it; do not just echo raw
fields. If it does not match, tell the officer what you tried and why it
failed, and ask them to confirm or correct it. Do not silently guess a second
time and do not fabricate an alternative you have not checked.

You speak to the officer in short turns and then stop. Ask one thing at a time.
Where the register gives a question and the reason it has no default, put both:
the officer is owed the argument, not just the question. When the question you
are asking is one of the register's decisions, call `present_options` with its
key as you ask it — this is what lets the interface show a card with real
priced options beside your words, keyed to exactly what you asked, rather than
guessed at from a fixed order that does not track how you actually sequence
the conversation. Do not call it for a decision you are not asking about this
turn, and do not call it again for one already recorded.

You cannot write the officer's reasons. `record_decision` takes the id of
something the officer actually said, and the harness copies that text verbatim
into the record. If what they said does not yet amount to a reason, ask them;
do not supply one on their behalf, and do not paraphrase what they gave you.

The figures you quote must come from a tool result. Do not estimate, round for
effect, or carry a number from one district to another."""


def system_stable() -> str:
    """The cached, session-independent prefix: skill, subagent roster, frame.

    Shared by both engines' system prompt builders below, so the two cannot
    drift from each other by construction.
    """
    return "\n\n---\n\n".join([
        skill("run"),
        skill("spatial"),
        "# The subagents this repository declares\n\n" + "\n".join(
            f"- **{a.name}** ({'read-only' if a.read_only else ', '.join(a.tools)}): "
            f"{a.description.splitlines()[0] if a.description else ''}"
            for a in agents()),
        FRAME,
    ])


def system_blocks(extra: str = "") -> list[dict]:
    """The system prompt for the raw Messages API: skill first and cached,
    session detail last."""
    blocks = [{"type": "text", "text": system_stable(),
               "cache_control": {"type": "ephemeral"}}]
    if extra:
        blocks.append({"type": "text", "text": extra})
    return blocks


def system_text(extra: str = "") -> str:
    """The system prompt as one plain string, for `ClaudeAgentOptions.system_prompt`
    — a bare string there replaces Claude Code's own default persona entirely,
    which is what an officer-facing interview needs (no "I am Claude Code, a
    CLI tool" framing leaking into the conversation). The Messages-API block
    shape with `cache_control` that `system_blocks` builds doesn't apply here;
    the SDK manages its own prompt caching.
    """
    stable = system_stable()
    return f"{stable}\n\n---\n\n{extra}" if extra else stable


@lru_cache(maxsize=None)
def agent_definition(name: str):
    """One `.claude/agents/<name>.md` file, as an SDK `AgentDefinition`.

    `background=False` always: a web turn is one HTTP round trip with no
    channel to receive a later async completion notification, so a delegated
    subagent has to block until it actually returns. (The SDK's own default,
    confirmed by testing, is to run a delegated subagent in the background and
    hand control back immediately — wrong for this turn-per-request shape.)
    """
    from claude_agent_sdk import AgentDefinition

    fields, body = _frontmatter(_read(AGENTS / f"{name}.md"))
    tools = [t.strip() for t in fields.get("tools", "").split(",") if t.strip()]
    return AgentDefinition(description=fields.get("description", "").strip(),
                           prompt=body.strip(), tools=tools or None,
                           background=False)


def _main() -> int:
    print(f"skills loaded: {', '.join(SKILLS)}")
    for name in SKILLS:
        print(f"  {name:<8} {len(skill(name)):>6,} chars")
    print("\nsubagents declared:")
    for a in agents():
        grant = ", ".join(a.tools) or "none"
        print(f"  {a.name:<16} {grant:<22} {'read-only' if a.read_only else 'CAN WRITE'}")
    blocks = system_blocks()
    print(f"\nsystem prompt: {sum(len(b['text']) for b in blocks):,} chars, "
          f"{len(blocks)} block(s), cached prefix = {len(blocks[0]['text']):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
