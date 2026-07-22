#!/usr/bin/env bash
set -Eeuo pipefail

SITE_DIR="${1:?usage: publish_pages_repository.sh SITE_DIR}"
PAGES_REPO="${PAGES_REPO:?PAGES_REPO is required}"
PAGES_REPO_BRANCH="${PAGES_REPO_BRANCH:-pages-data}"
PAGES_REPO_TOKEN="${PAGES_REPO_TOKEN:?PAGES_REPO_TOKEN is required}"
PAGES_DEPLOY_EVENT_TYPE="${PAGES_DEPLOY_EVENT_TYPE:-pages-data-updated}"
WORK_DIR="${PIF_PAGES_SYNC_WORK_DIR:-/tmp/pif_pages_repo}"
DISPATCH_ENABLED="${PIF_PAGES_DISPATCH_ENABLED:-true}"
GIT_URL="${PIF_PAGES_GIT_URL:-https://x-access-token:${PAGES_REPO_TOKEN}@github.com/${PAGES_REPO}.git}"
API_URL="${PIF_PAGES_DISPATCH_URL:-https://api.github.com/repos/${PAGES_REPO}/dispatches}"

fail(){ echo "[Pages发布失败] $*" >&2; exit 1; }
[[ -s "$SITE_DIR/index.html" ]] || fail "Pages site missing index.html: $SITE_DIR"
[[ "$PAGES_REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fail "Invalid PAGES_REPO: $PAGES_REPO"
[[ "$PAGES_REPO_BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "Invalid PAGES_REPO_BRANCH: $PAGES_REPO_BRANCH"
[[ "$PAGES_REPO_BRANCH" != main ]] || fail "Generated Pages data branch must not be main"
[[ "$PAGES_DEPLOY_EVENT_TYPE" == pages-data-updated ]] || fail "Unexpected Pages deploy event: $PAGES_DEPLOY_EVENT_TYPE"

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

prepare_checkout(){
  rm -rf "$WORK_DIR"
  if git ls-remote --exit-code --heads "$GIT_URL" "$PAGES_REPO_BRANCH" >/dev/null 2>&1; then
    git clone --quiet --branch "$PAGES_REPO_BRANCH" --single-branch "$GIT_URL" "$WORK_DIR"
    [[ -f "$WORK_DIR/.pages-data-branch" ]] \
      || fail "Existing branch $PAGES_REPO_BRANCH is not marked as machine-owned"
  else
    git clone --quiet "$GIT_URL" "$WORK_DIR"
    cd "$WORK_DIR"
    git checkout --orphan "$PAGES_REPO_BRANCH"
    git rm -rf . >/dev/null 2>&1 || true
    find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
  fi
  cd "$WORK_DIR"
  git config user.name "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
}

apply_site_snapshot(){
  cd "$WORK_DIR"
  mkdir -p public
  rsync -a --delete --exclude '.gitkeep' "$SITE_DIR/" public/
  touch public/.nojekyll
  printf '%s\n' "machine-owned generated branch: $PAGES_REPO_BRANCH" > .pages-data-branch
  git add -A public .pages-data-branch
  if git diff --cached --quiet; then
    echo "Public Pages content unchanged."
  else
    git commit -m "chore: publish pathogen intelligence pages"
  fi
}

prepare_checkout
apply_site_snapshot

if ! git push origin "HEAD:${PAGES_REPO_BRANCH}"; then
  echo "::warning::Pages data branch advanced concurrently; rebasing the full generated snapshot once."
  git fetch origin "$PAGES_REPO_BRANCH"
  git reset --hard "origin/${PAGES_REPO_BRANCH}"
  apply_site_snapshot
  git push origin "HEAD:${PAGES_REPO_BRANCH}"
fi

PAGES_DATA_SHA="$(git rev-parse HEAD)"
echo "Published public site to ${PAGES_REPO}@${PAGES_REPO_BRANCH} (${PAGES_DATA_SHA})"

if [[ "${DISPATCH_ENABLED,,}" == true ]]; then
  PAYLOAD="$(mktemp /tmp/pif-pages-dispatch.XXXXXX.json)"
  trap 'rm -f "$PAYLOAD"' EXIT
  PAGES_REPO_BRANCH="$PAGES_REPO_BRANCH" PAGES_DATA_SHA="$PAGES_DATA_SHA" PAGES_DEPLOY_EVENT_TYPE="$PAGES_DEPLOY_EVENT_TYPE" \
  python - "$PAYLOAD" <<'PY'
import json, os, sys
payload = {
    "event_type": os.environ["PAGES_DEPLOY_EVENT_TYPE"],
    "client_payload": {
        "branch": os.environ["PAGES_REPO_BRANCH"],
        "commit_sha": os.environ["PAGES_DATA_SHA"],
    },
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False)
PY
  curl --fail-with-body --silent --show-error \
    -X POST \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $PAGES_REPO_TOKEN" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$API_URL" \
    --data-binary "@$PAYLOAD"
  echo "Triggered ${PAGES_DEPLOY_EVENT_TYPE} for ${PAGES_REPO}@${PAGES_REPO_BRANCH}"
fi
