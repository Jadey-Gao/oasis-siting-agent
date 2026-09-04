"""A conversational front end for the siting agent.

Additive in full: this package reads from `siting/`, `skills/` and
`.claude/agents/`, calls `python -m siting.cli` as a subprocess, and writes only
under `sessions/`. See README.md for the contract.
"""
