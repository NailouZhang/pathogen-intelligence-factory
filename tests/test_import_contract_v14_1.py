from __future__ import annotations

import importlib
from pathlib import Path


def test_formal_package_import_resolves_to_current_src_tree():
    package = importlib.import_module("pifactory")
    package_path = Path(package.__file__).resolve()
    expected_package = (Path(__file__).resolve().parents[1] / "src" / "pifactory").resolve()
    assert package_path == expected_package / "__init__.py"


def test_project_uses_one_formal_package_namespace():
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for base in (root / "scripts", root / "tests"):
        for path in base.rglob("*.py"):
            if path == Path(__file__).resolve():
                continue
            text = path.read_text(encoding="utf-8")
            if "src.pifactory" in text:
                offenders.append(str(path.relative_to(root)))
    assert offenders == []
