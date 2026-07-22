from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ROOT = ROOT.parent


def _resolve_repo_path(env_name: str, bundle_sibling: Path) -> Path:
    configured = os.getenv(env_name, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return bundle_sibling.resolve()


PAGES_ROOT = _resolve_repo_path(
    "PAGES_REPO_DIR",
    SYSTEM_ROOT / "pathogen-intelligence-pages",
)


def test_workflow_runs_at_beijing_0200_and_has_no_google_cse():
    text = (ROOT / ".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    pages = (PAGES_ROOT / ".github/workflows/deploy-pages.yml").read_text(encoding="utf-8")
    assert "cron: '0 18 * * *'" in text or 'cron: "0 18 * * *"' in text
    assert "deploy-pages@v4" not in text
    assert "publish_pages_repository.sh" in text
    assert "PAGES_REPO_TOKEN" in text
    assert "deploy-pages@v4" in pages
    assert "intelligence-data" in text
    assert "GOOGLE_CSE" not in text
    assert "for PROFILE_ID" in text


def test_wechat_dispatch_is_best_effort_and_cannot_block_pages():
    text = (ROOT / ".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    assert "if curl --fail-with-body" in text
    assert "WeChat dispatch failed" in text
    assert "Pages generation will continue" in text
    assert text.index("if curl --fail-with-body") < text.index("Build public static portal")
    assert "continue-on-error: true" in text
