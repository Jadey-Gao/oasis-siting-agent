#!/usr/bin/env bash
# PostToolUse. Records what happened, and notices two things worth noticing.
set -uo pipefail
INPUT="${1:-}"; RESPONSE="${2:-}"
LOG_DIR="harness/logs"; mkdir -p "$LOG_DIR"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

printf '%s\tPostToolUse\t%s\n' "$TS" "$(printf '%s' "$INPUT" | head -c 400)" \
  >> "$LOG_DIR/tool_calls.log"

# A run that stopped for a missing decision is the design working, not a fault.
# Recording it separately keeps that distinction visible in the log.
case "$RESPONSE" in
  *"cannot proceed"*|*"decisions have not been made"*)
    printf '%s\tCHECKPOINT\ta decision was required and the run stopped\n' "$TS" \
      >> "$LOG_DIR/checkpoints.log" ;;
esac

# A refused gate removes a source from the analysis. It must reach the output.
case "$RESPONSE" in
  *"[gate] refused"*|*"REFUSE"*)
    printf '%s\tGATE\t%s\n' "$TS" "$(printf '%s' "$RESPONSE" | grep -o 'refused.*' | head -c 300)" \
      >> "$LOG_DIR/checkpoints.log" ;;
esac
exit 0
