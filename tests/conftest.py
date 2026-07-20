from __future__ import annotations

import pytest


LLM_SECRET_ENV_NAMES = (
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "MISTRAL_API_KEY",
    "SILICONFLOW_API_KEY",
    "BIGMODEL_API_KEY",
    "DEEPSEEK_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolate_external_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests independent of live browsers, credentials, and provider state.

    Tests that exercise a provider explicitly pass a fake key or opt in with
    ``monkeypatch.setenv`` after this fixture has removed ambient production
    credentials. This prevents a developer shell or GitHub secret environment
    from turning deterministic unit tests into live LLM/network calls.
    """
    monkeypatch.setenv("PIF_NEWS_BROWSER_ENABLED", "false")
    for name in LLM_SECRET_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("PIF_PROVIDER_STATE_FILE", raising=False)
