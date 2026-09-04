# web/ — a conversational front end for the siting agent

Everything under `web/` and `sessions/` is additive. Nothing outside these two
directories is modified by anything in here.

The contract, in three lines:

- **Read.** Questions, options, the reasons a decision has no default, the stage
  order, the agents' remits: all read from `siting/`, `skills/` and
  `.claude/agents/`. Never copied, never restated.
- **Call.** The analysis runs as `python -m siting.cli` in a subprocess. The web
  process has no ability to alter it. That guarantee is enforced by the operating
  system, not by discipline.
- **Draw.** `results.json` is the only thing read back. Both documents already
  compile from it, so the page cannot disagree with the PDF.

Delete `web/` and `sessions/` and the repository is exactly as it was.

## What is not allowed in here

- Importing anything from `siting/` for any purpose other than reading a
  declaration (`decisions.REGISTER`, `overrides` verb names). No calling into the
  analysis directly.
- Restating a question, an option, or a threshold that already exists in
  `siting/decisions.py`. Two copies of a value-laden question is one copy too many.
- Writing to `decisions/`, `overrides/`, `runs/`, `cache/` or `handbooks/`. A
  session writes only under `sessions/<id>/`.
- Passing an officer's stated reason through a language model. It is recorded
  verbatim or it is not recorded.
