#!/usr/bin/env bash
# Build the Acme mini-org demo repo: copies examples/acme-mini-org into a new
# directory and creates a git history where the four migrations ship one per
# commit on branch main.
#
# Usage: build_fixture.sh <target-dir>
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $(basename "$0") <target-dir>" >&2
  exit 2
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/acme-mini-org"
TARGET="$1"

if [[ -e "$TARGET" ]]; then
  echo "error: target '$TARGET' already exists" >&2
  exit 1
fi
if [[ ! -d "$SRC" ]]; then
  echo "error: template not found at '$SRC'" >&2
  exit 1
fi

mkdir -p "$(dirname "$TARGET")"
cp -R "$SRC" "$TARGET"
TARGET="$(cd "$TARGET" && pwd)"
# Drop local build noise if the template was ever run in place.
find "$TARGET" \( -name '__pycache__' -o -name '.pytest_cache' \) -type d -prune -exec rm -rf {} +

cd "$TARGET"
git init -q -b main

export GIT_AUTHOR_NAME="Acme Dev" GIT_AUTHOR_EMAIL="dev@acme.example"
export GIT_COMMITTER_NAME="Acme Dev" GIT_COMMITTER_EMAIL="dev@acme.example"
git config user.name "Acme Dev"
git config user.email "dev@acme.example"
git config commit.gpgsign false

V=services/ledger-api/migrations/versions

commit() { # <iso-date> <message>
  GIT_AUTHOR_DATE="$1" GIT_COMMITTER_DATE="$1" git commit -q -m "$2"
}

# 1: scaffolding, models (final form), orders migration.
git add -A -- . \
  ":(exclude)$V/0002_*.py" ":(exclude)$V/0003_*.py" ":(exclude)$V/0004_*.py" \
  ":(exclude)services/ledger-api/seed.sql"
commit "2026-01-12T09:00:00+00:00" "ledger-api: scaffold service and create orders table"

# 2
git add "$V"/0002_*.py
commit "2026-02-03T09:00:00+00:00" "ledger-api: add inventory_movements"

# 3
git add "$V"/0003_*.py
commit "2026-03-02T09:00:00+00:00" "ledger-api: add ledger_entries and audit_log"

# 4
git add "$V"/0004_*.py services/ledger-api/seed.sql
commit "2026-04-14T09:00:00+00:00" "ledger-api: add order_import_staging and seed data"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "error: uncommitted files left in fixture:" >&2
  git status --porcelain >&2
  exit 1
fi

echo "$TARGET"
