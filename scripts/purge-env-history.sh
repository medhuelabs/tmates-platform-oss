#!/usr/bin/env bash

set -euo pipefail

TARGET_FILE=".env.staging"

echo "==> Verifying repo is clean…"
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ERROR: Working tree has changes. Commit or stash them before running cleanup." >&2
  exit 1
fi

if [ ! -d .git ]; then
  echo "ERROR: This script must run from the repository root." >&2
  exit 1
fi

if ! command -v git-filter-repo >/dev/null 2>&1; then
  echo "ERROR: git-filter-repo not found on PATH. Install it (e.g. brew install git-filter-repo)." >&2
  exit 1
fi

ORIGIN_URL="$(git remote get-url origin 2>/dev/null || true)"
if [ -n "${ORIGIN_URL}" ]; then
  echo "==> Captured current origin remote: ${ORIGIN_URL}"
else
  echo "==> No origin remote configured before rewrite."
fi

echo "==> Rewriting history to drop ${TARGET_FILE}…"
git filter-repo --force --path "${TARGET_FILE}" --invert-paths

echo "==> Cleaning backup refs & reflogs…"
rm -rf .git/refs/original/
git reflog expire --expire=now --all

echo "==> Running aggressive GC…"
git gc --prune=now --aggressive

if [ -n "${ORIGIN_URL}" ] && ! git remote get-url origin >/dev/null 2>&1; then
  echo "==> Restoring origin remote"
  git remote add origin "${ORIGIN_URL}"
fi

echo "==> Double-checking ${TARGET_FILE} is gone…"
if git ls-tree -r HEAD | grep -q "${TARGET_FILE}"; then
  echo "ERROR: ${TARGET_FILE} still present in current tree." >&2
  exit 1
fi

if git log --name-only | grep -q "${TARGET_FILE}"; then
  echo "ERROR: ${TARGET_FILE} still referenced in history." >&2
  exit 1
fi

echo "==> Force-pushing rewritten history (branches & tags)…"
git push origin --force --all
git push origin --force --tags

cat <<'MSG'

✔ History rewritten and pushed.

Next steps:
  • Rotate every secret contained in .env.staging (Supabase, OpenAI, AWS, Stripe, Logfire, etc.).
  • Tell collaborators to delete old clones and re-clone (history changed).
  • Enable secret scanning (git-secrets, ggshield, GitHub scanning) to prevent future leaks.

MSG
