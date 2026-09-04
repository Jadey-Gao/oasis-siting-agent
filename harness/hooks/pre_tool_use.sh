#!/usr/bin/env bash
# PreToolUse. Refuses a call before it happens, and logs every one.
#
# The permission block in settings.json is the first line of defence and covers
# whole classes of command. This hook covers what a pattern cannot: an attempt to
# edit the instrument to fit the answer, or to fabricate a retrieval that failed.

set -uo pipefail
INPUT="${1:-}"
LOG_DIR="harness/logs"
mkdir -p "$LOG_DIR"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

printf '%s\tPreToolUse\t%s\n' "$TS" "$INPUT" >> "$LOG_DIR/tool_calls.log"

refuse() {
  printf '%s\tREFUSED\t%s\t%s\n' "$TS" "$1" "$INPUT" >> "$LOG_DIR/refusals.log"
  echo "REFUSED by pre_tool_use: $1" >&2
  exit 2
}

# The instrument is not editable during a run. If a tool is wrong, that is a
# finding to report, not a file to change: silently adjusting the analysis to
# produce a wanted answer is the failure this whole structure exists to prevent.
case "$INPUT" in
  *"siting/"*">"*|*"handbooks/"*">"*|*"sed -i"*"siting/"*|*"sed -i"*"handbooks/"*)
    refuse "the analysis code and the source handbooks are read-only during a run" ;;
esac

# A decisions file records what a person decided. The agent may propose one, but
# writing it from a shell command inside a run bypasses the record of who decided.
case "$INPUT" in
  *"decisions/"*">"*|*"echo"*"decided_by"*)
    refuse "write a decisions file with the Write tool so its authorship is recorded" ;;
esac

# Nothing leaves this machine from inside a run.
case "$INPUT" in
  *"git push"*|*"gh pr"*|*"gh release"*|*"curl -X POST"*|*"curl -d"*)
    refuse "a run does not publish; delivery is a separate, deliberate act" ;;
esac

# Destructive removal, including the cached retrievals that make a run reproducible.
case "$INPUT" in
  *"rm -rf"*|*"rm -r "*|*"rmdir"*)
    refuse "removal is not part of a run; the cache is what makes it reproducible" ;;
esac

exit 0
