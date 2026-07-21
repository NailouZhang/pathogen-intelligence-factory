from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ROOT = ROOT.parent


def _resolve_repo_path(
    env_name: str,
    bundle_sibling: Path,
    installed_default: Path,
) -> Path:
    """Resolve cross-repository contract paths in bundles and installed layouts."""
    configured = os.getenv(env_name, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if bundle_sibling.is_dir():
        return bundle_sibling.resolve()
    return installed_default.expanduser().resolve()


PAGES = _resolve_repo_path(
    "PAGES_REPO_DIR",
    SYSTEM_ROOT / "pathogen-intelligence-pages",
    Path.home() / "github-projects" / "pathogen-intelligence-pages",
)

WECHAT_HOME = Path(
    os.getenv(
        "PATHOGEN_WECHAT_HOME",
        str(Path.home() / "pathogen-wechat-publisher"),
    )
).expanduser()

PUBLISHER = _resolve_repo_path(
    "PUBLISHER_REPO_DIR",
    SYSTEM_ROOT / "pathogen-wechat-publisher",
    WECHAT_HOME / "repository",
)


def _read_required(path: Path) -> str:
    assert path.is_file(), (
        f"Required three-repository contract file is missing: {path}. "
        "Set PAGES_REPO_DIR/PUBLISHER_REPO_DIR when using custom paths."
    )
    return path.read_text(encoding="utf-8")


def test_private_factory_syncs_only_static_site_to_public_pages_repo() -> None:
    workflow = _read_required(ROOT / ".github/workflows/daily-intelligence.yml")
    sync = _read_required(ROOT / "scripts/publish_pages_repository.sh")
    assert "PAGES_REPO_TOKEN" in workflow
    assert "publish_pages_repository.sh" in workflow
    assert "deploy-pages@v4" not in workflow
    assert "rsync" in sync
    assert "public/" in sync
    assert "Forbidden public path" in sync
    assert "config/vocabularies" in sync
    assert "data/audit" in sync


def test_public_pages_repo_contains_only_static_site_and_own_deployer() -> None:
    workflow = _read_required(PAGES / ".github/workflows/deploy-pages.yml")
    assert "actions/configure-pages@v5" in workflow
    assert "actions/upload-pages-artifact@v4" in workflow
    assert "actions/deploy-pages@v4" in workflow
    assert not (PAGES / "src").exists()
    assert not (PAGES / "config/vocabularies").exists()


def test_private_publisher_requires_source_repo_token_for_private_factory() -> None:
    settings = _read_required(PUBLISHER / "src/wechat_publisher/config.py")
    downloader = _read_required(PUBLISHER / "src/wechat_publisher/package.py")
    example = _read_required(PUBLISHER / "config/publisher.env.example")
    assert "SOURCE_REPO_TOKEN" in settings
    assert "Authorization" in downloader
    assert "SOURCE_REPO_TOKEN" in example