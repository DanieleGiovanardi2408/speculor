#!/usr/bin/env bash
# Push the local database (with the gitignored config stored inside it) to the orphan `data`
# branch, which the daily GitHub Actions workflow reads and updates.
# Never touches `main`: everything happens in a temporary git worktree.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
DB="data/radar.db"
BRANCH="data"
REMOTE="${REMOTE:-origin}"

if [[ ! -f "$DB" ]]; then
  echo "error: $DB not found. Create it first (make seed, or uv run radar init-db)." >&2
  exit 1
fi

echo "Storing config/{profile,companies,rules}.yaml in the settings table..."
RADAR_DATABASE_URL="sqlite:///$PWD/$DB" uv run radar config push

TMP="$(mktemp -d)"
WT="$TMP/data-branch"
cleanup() { git worktree remove --force "$WT" 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

if git ls-remote --exit-code --heads "$REMOTE" "$BRANCH" >/dev/null 2>&1; then
  git fetch --quiet "$REMOTE" "$BRANCH"
  git worktree add --quiet --detach "$WT" "$REMOTE/$BRANCH"
  git -C "$WT" checkout --quiet -B "$BRANCH"
else
  echo "Creating orphan branch '$BRANCH'..."
  git worktree add --quiet --detach "$WT"
  git -C "$WT" checkout --quiet --orphan "$BRANCH"
  git -C "$WT" rm -rf --quiet . >/dev/null 2>&1 || true
  git -C "$WT" clean -fdxq
fi

cp "$DB" "$WT/radar.db"
cat > "$WT/README.md" <<'MD'
# data branch

Orphan branch holding the Radar database (`radar.db`) and daily reports (`reports/`).
Written by `make bootstrap-data` and the daily workflow. Never merge into `main`.
Delete this branch before making the repository public.
MD

git -C "$WT" add -f radar.db README.md
if git -C "$WT" diff --cached --quiet; then
  echo "Nothing changed."
  exit 0
fi
# --no-verify: this branch is MEANT to hold the database, which the privacy hook blocks on main.
git -C "$WT" commit --quiet --no-verify -m "chore(data): bootstrap $(date +%F)"
git -C "$WT" push --quiet "$REMOTE" "HEAD:refs/heads/$BRANCH"
echo "Pushed $DB to '$BRANCH'."
