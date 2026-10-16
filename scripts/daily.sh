#!/bin/bash
# Daily Radar run, started by launchd (see `make schedule-install`). Local only.
# Appends to logs/daily.log (counts only: `radar run` never prints names or titles).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p logs
LOG="logs/daily.log"
# Keep the log small: rotate at ~2 MB.
if [[ -f "$LOG" && $(stat -f%z "$LOG") -gt 2000000 ]]; then mv "$LOG" "$LOG.1"; fi

# One run at a time (launchd may fire a missed run on wake while one is in progress).
LOCK="logs/.daily.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(date '+%F %T') skipped: another run is in progress" >> "$LOG"
  exit 0
fi
trap 'rmdir "$LOCK"' EXIT

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
{
  echo "=== $(date '+%F %T') daily run ==="
  uv run --frozen --all-extras radar run
  echo "=== $(date '+%F %T') exit $? ==="
} >> "$LOG" 2>&1
