from __future__ import annotations

import ast
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ROOT = ROOT.parent


def _repo_path(env_name: str, sibling: str, installed: Path) -> Path:
    configured = os.getenv(env_name, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    candidate = SYSTEM_ROOT / sibling
    if candidate.is_dir():
        return candidate.resolve()
    return installed.expanduser().resolve()


PAGES = _repo_path(
    "PAGES_REPO_DIR",
    "pathogen-intelligence-pages",
    Path.home() / "github-projects" / "pathogen-intelligence-pages",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"required contract file missing: {path}"
    return path.read_text(encoding="utf-8")


def test_pages_main_and_machine_data_branch_are_strictly_separated() -> None:
    daily = _read(ROOT / ".github/workflows/daily-intelligence.yml")
    rebuild = _read(ROOT / ".github/workflows/publish-pages-only.yml")
    sync = _read(ROOT / "scripts/publish_pages_repository.sh")
    deploy = _read(PAGES / ".github/workflows/deploy-pages.yml")

    assert "PAGES_REPO_BRANCH: ${{ vars.PAGES_REPO_BRANCH || 'pages-data' }}" in daily
    assert "PAGES_REPO_BRANCH: ${{ vars.PAGES_REPO_BRANCH || 'pages-data' }}" in rebuild
    assert 'PAGES_REPO_BRANCH="${PAGES_REPO_BRANCH:-pages-data}"' in sync
    assert '[[ "$PAGES_REPO_BRANCH" != main ]]' in sync
    assert "pages-data-updated" in sync
    assert "repos/${PAGES_REPO}/dispatches" in sync

    assert "repository_dispatch:" in deploy
    assert "types: [pages-data-updated]" in deploy
    assert "default: pages-data" in deploy
    assert "ref: ${{ steps.source.outputs.branch }}" in deploy
    assert "Generated Pages data must never be deployed from main" in deploy
    assert "path: public" in deploy


def test_pages_main_template_contains_no_generated_site_tree() -> None:
    assert not (PAGES / "public" / "profiles").exists()
    assert not (PAGES / "public" / "portal.json").exists()
    assert not (PAGES / "public" / "index.html").exists()
    readme = _read(PAGES / "README.md")
    assert "`main`" in readme
    assert "`pages-data`" in readme


def test_factory_tests_never_read_release_control_shells_from_parent() -> None:
    """Prevent recurrence of ROOT.parent/public_manager.sh style failures."""
    forbidden = {
        "system_manager.sh",
        "public_manager.sh",
        "pages_manager.sh",
        "private_manager.sh",
        "install_three_repos.sh",
        "validate_bundle.sh",
    }
    violations: list[str] = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in {
                "read_text",
                "read_bytes",
                "is_file",
                "exists",
                "open",
            }:
                continue
            strings = {
                value.value
                for value in ast.walk(func.value)
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
            }
            matched = forbidden & strings
            if matched:
                violations.append(f"{path.name}:{node.lineno}:{','.join(sorted(matched))}")
    assert not violations, "Factory tests crossed into release control layer: " + "; ".join(violations)
