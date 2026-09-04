#!/usr/bin/env bash
# Stop. Leaves an account of the session behind, so an interrupted run is
# resumable and a completed one is auditable without opening a PDF.
set -uo pipefail
LOG_DIR="harness/logs"; mkdir -p "$LOG_DIR" memory
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

LATEST="$(ls -dt runs/*/ 2>/dev/null | head -1)"
{
  echo "# Session state"
  echo
  echo "- Ended: $TS"
  if [ -n "$LATEST" ]; then
    echo "- Latest run: \`$(basename "$LATEST")\`"
    if [ -f "${LATEST}handoff.json" ]; then
      STAGE="$(grep -o '"stage": *"[^"]*"' "${LATEST}handoff.json" | head -1 | sed 's/.*: *"//;s/"//')"
      echo "- Stage on exit: **${STAGE:-unknown}**"
      echo "- Resume: \`python -m siting.cli ... --resume $(basename "$LATEST")\`"
    fi
    for f in evidence-bundle.pdf assessment.pdf; do
      [ -f "${LATEST}${f}" ] && echo "- Issued: \`${LATEST}${f}\`"
    done
  else
    echo "- No run directory found; nothing was issued this session."
  fi
  if [ -s "$LOG_DIR/checkpoints.log" ]; then
    echo
    echo "## Checkpoints reached this session"
    echo
    tail -20 "$LOG_DIR/checkpoints.log" | sed 's/^/- /'
  fi
  if [ -s "$LOG_DIR/refusals.log" ]; then
    echo
    echo "## Refused by the harness"
    echo
    tail -20 "$LOG_DIR/refusals.log" | sed 's/^/- /'
  fi
} > memory/SESSION.md
printf '%s\tStop\tsession account written to memory/SESSION.md\n' "$TS" \
  >> "$LOG_DIR/tool_calls.log"
exit 0
