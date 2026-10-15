#!/usr/bin/env bash
# Download the latest database and reports written by the daily workflow (orphan `data` branch).
# Your current local DB is backed up first to data/backups/. Records the pulled commit in
# data/.data_sha so `make push-data` can refuse to overwrite a newer CI run.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
REMOTE="${REMOTE:-origin}"
git fetch --quiet "$REMOTE" data
SHA="$(git rev-parse "$REMOTE/data")"
mkdir -p data/backups data/reports
if [[ -f data/radar.db ]]; then
  cp data/radar.db "data/backups/radar-$(date +%Y%m%d-%H%M%S).db"
fi
git show "$REMOTE/data:radar.db" > data/radar.db
for f in $(git ls-tree --name-only "$REMOTE/data" reports/ 2>/dev/null || true); do
  git show "$REMOTE/data:$f" > "data/$f"
done
echo "$SHA" > data/.data_sha
echo "Pulled data branch ${SHA:0:7} into data/radar.db (previous DB backed up in data/backups/)."
