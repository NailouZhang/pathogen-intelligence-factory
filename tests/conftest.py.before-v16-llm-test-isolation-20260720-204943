from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_live_browser_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests independent of Chromium and external news pages.

    Browser-specific tests explicitly opt in with ``PIF_NEWS_BROWSER_ENABLED=true``.
    This prevents an installed Playwright browser from bypassing Fake/BrokenHTTP
    clients and turning deterministic RSS provenance tests into live network tests.
    """
    monkeypatch.setenv("PIF_NEWS_BROWSER_ENABLED", "false")
