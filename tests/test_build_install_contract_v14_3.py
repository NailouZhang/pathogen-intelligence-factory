from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_build_toolchain_is_installed_before_editable_package() -> None:
    script = (ROOT / "scripts" / "install_python_project.sh").read_text(encoding="utf-8")
    build_install = script.index('pip install --upgrade -r "$ROOT/requirements-build.txt"')
    backend_check = script.index('"setuptools.build_meta"')
    editable_install = script.index('pip install --no-build-isolation --no-deps -e "$ROOT"')
    assert build_install < backend_check < editable_install


def test_both_github_workflows_use_the_shared_installer() -> None:
    for name in ("daily-intelligence.yml", "ci.yml"):
        workflow = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "PIF_PYTHON_BIN=python bash scripts/install_python_project.sh editable" in workflow
        assert "requirements-build.txt" in workflow
        assert "requirements.txt" in workflow
        assert "python -m pip install --no-build-isolation --no-deps -e ." not in workflow


def test_build_requirements_include_setuptools_backend() -> None:
    requirements = (ROOT / "requirements-build.txt").read_text(encoding="utf-8").lower()
    assert "setuptools>=69" in requirements
    assert "wheel" in requirements
    assert "pip" in requirements


def test_bootstrap_uses_fixed_prefix_python_and_shared_installer() -> None:
    bootstrap = (ROOT / "scripts" / "bootstrap_dev.sh").read_text(encoding="utf-8")
    assert 'ENV_PREFIX="${PIF_ENV_PREFIX:-$ROOT/.conda-env}"' in bootstrap
    assert 'PIF_PYTHON_BIN="$ENV_PREFIX/bin/python"' in bootstrap
    assert 'install_python_project.sh" editable' in bootstrap
