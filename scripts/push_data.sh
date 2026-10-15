#!/usr/bin/env bash
# Upload your local database (approvals, notes, applications) to the `data` branch so the
# next daily run starts from it. Refuses if the daily workflow wrote a newer version since
# your last `make pull-data` (pull first, then redo your edits).
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
REMOTE="${REMOTE:-origin}"
git fetch --quiet "$REMOTE" data
REMOTE_SHA="$(git rev-parse "$REMOTE/data")"
LOCAL_SHA="$(cat data/.data_sha 2>/dev/null || echo none)"
if [[ "$REMOTE_SHA" != "$LOCAL_SHA" && "${FORCE:-0}" != "1" ]]; then
  echo "error: the data branch changed since your last pull (${REMOTE_SHA:0:7})." >&2
  echo "Run 'make pull-data' first (your local DB gets backed up), or FORCE=1 to overwrite." >&2
  exit 1
fi
./scripts/bootstrap_data.sh
git fetch --quiet "$REMOTE" data
git rev-parse "$REMOTE/data" > data/.data_sha
