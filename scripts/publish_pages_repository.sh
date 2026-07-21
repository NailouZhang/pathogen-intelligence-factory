#!/usr/bin/env bash
set -Eeuo pipefail

SITE_DIR="${1:?usage: publish_pages_repository.sh SITE_DIR}"
PAGES_REPO="${PAGES_REPO:?PAGES_REPO is required}"
PAGES_REPO_BRANCH="${PAGES_REPO_BRANCH:-main}"
PAGES_REPO_TOKEN="${PAGES_REPO_TOKEN:?PAGES_REPO_TOKEN is required}"
WORK_DIR="${PIF_PAGES_SYNC_WORK_DIR:-/tmp/pif_pages_repo}"

[[ -s "$SITE_DIR/index.html" ]] || { echo "Pages site missing index.html: $SITE_DIR" >&2; exit 1; }
[[ "$PAGES_REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "Invalid PAGES_REPO: $PAGES_REPO" >&2; exit 1; }

python - "$SITE_DIR" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1]).resolve()
allowed_top = {"index.html", "portal.json", "feed.xml", "robots.txt", ".nojekyll", "profiles", "assets", "images"}
for child in root.iterdir():
    if child.name not in allowed_top:
        raise SystemExit(f"Unexpected top-level public artifact: {child.name}")
for path in root.rglob("*"):
    if not path.is_file():
        continue
    rel = path.relative_to(root).as_posix()
    if path.suffix.lower() in {".py", ".pyc", ".yaml", ".yml", ".env", ".key", ".pem"}:
        raise SystemExit(f"Forbidden public file type: {rel}")
    if any(token in rel for token in ("data/audit", "config/vocabularies", "provider_quota", "review_vocabulary")):
        raise SystemExit(f"Forbidden public path: {rel}")
print("PUBLIC_WHITELIST_OK")
PY

rm -rf "$WORK_DIR"
AUTH="https://x-access-token:${PAGES_REPO_TOKEN}@github.com/${PAGES_REPO}.git"
if git ls-remote --exit-code --heads "$AUTH" "$PAGES_REPO_BRANCH" >/dev/null 2>&1; then
  git clone --quiet --branch "$PAGES_REPO_BRANCH" --single-branch "$AUTH" "$WORK_DIR"
else
  git clone --quiet "$AUTH" "$WORK_DIR"
  cd "$WORK_DIR"
  git checkout --orphan "$PAGES_REPO_BRANCH"
  git rm -rf . >/dev/null 2>&1 || true
fi
cd "$WORK_DIR"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
mkdir -p public
rsync -a --delete --exclude '.gitkeep' "$SITE_DIR/" public/
touch public/.nojekyll
git add -A public
if git diff --cached --quiet; then
  echo "Public Pages content unchanged."
  exit 0
fi
git commit -m "chore: publish pathogen intelligence pages"
git push origin "HEAD:${PAGES_REPO_BRANCH}"
echo "Published public site to ${PAGES_REPO}@${PAGES_REPO_BRANCH}"
