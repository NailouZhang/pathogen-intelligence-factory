from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pifactory.llm import DEFAULT_MODELS, LLMError, LLMRouter, OPENAI_COMPATIBLE_PROVIDERS, classify_llm_failure


class FakeResponse:
    def __init__(self, body: dict[str, Any]):
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class RecordingHTTP:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.gets: list[tuple[str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append((method, url, kwargs))
        model = kwargs.get("json", {}).get("model", "")
        return FakeResponse({
            "model": model,
            "choices": [{"message": {"content": '{"status":"ok"}'}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        })

    def get_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        self.gets.append((url, kwargs))
        if url.endswith("/user/balance"):
            return {
                "is_available": True,
                "balance_infos": [{
                    "currency": "CNY",
                    "total_balance": "1.50",
                    "granted_balance": "1.00",
                    "topped_up_balance": "0.50",
                }],
            }
        return {"data": []}


def test_provider_defaults_and_full_endpoint_normalization(monkeypatch):
    assert OPENAI_COMPATIBLE_PROVIDERS["bigmodel"]["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert OPENAI_COMPATIBLE_PROVIDERS["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert DEFAULT_MODELS["bigmodel"] == ["glm-4.7-flash"]
    assert DEFAULT_MODELS["deepseek"] == ["deepseek-v4-flash"]
    monkeypatch.setenv("BIGMODEL_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions/")
    router = LLMRouter(RecordingHTTP())
    assert router.provider_base_url("bigmodel") == "https://open.bigmodel.cn/api/paas/v4"
    assert router.provider_base_url("deepseek") == "https://api.deepseek.com"


def test_bigmodel_and_deepseek_chat_payloads_disable_thinking(monkeypatch):
    monkeypatch.setenv("PIF_LLM_DISABLE_THINKING", "true")
    monkeypatch.setenv("PIF_DEEPSEEK_GRANTED_BALANCE_ONLY", "true")
    monkeypatch.setenv("PIF_DEEPSEEK_MIN_GRANTED_BALANCE", "0.10")
    http = RecordingHTTP()
    router = LLMRouter(http, bigmodel_key="bm-key", deepseek_key="ds-key")
    router._openai_compatible_call("bigmodel", "glm-4.7-flash", "Return JSON", "{}", 0.0)
    router._openai_compatible_call("deepseek", "deepseek-v4-flash", "Return JSON", "{}", 0.0)
    assert http.requests[0][1] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert http.requests[0][2]["json"]["thinking"] == {"type": "disabled"}
    assert http.requests[1][1] == "https://api.deepseek.com/chat/completions"
    assert http.requests[1][2]["json"]["thinking"] == {"type": "disabled"}
    assert http.requests[1][2]["json"]["response_format"] == {"type": "json_object"}


def test_deepseek_granted_balance_guard(monkeypatch):
    class NoGrantHTTP(RecordingHTTP):
        def get_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
            if url.endswith("/user/balance"):
                return {
                    "is_available": True,
                    "balance_infos": [{
                        "currency": "CNY",
                        "total_balance": "5.00",
                        "granted_balance": "0.00",
                        "topped_up_balance": "5.00",
                    }],
                }
            return {"data": []}

    monkeypatch.setenv("PIF_DEEPSEEK_GRANTED_BALANCE_ONLY", "true")
    router = LLMRouter(NoGrantHTTP(), deepseek_key="ds-key")
    try:
        router._openai_compatible_call("deepseek", "deepseek-v4-flash", "Return JSON", "{}", 0.0)
    except LLMError as exc:
        assert exc.category == "quota_exhausted"
    else:
        raise AssertionError("paid-only DeepSeek balance should be protected")


def test_provider_order_and_workflow_contract():
    router = LLMRouter(RecordingHTTP())
    for purpose in ("extract", "rescue", "overview", "relevance"):
        order = router.provider_order(purpose)
        assert "bigmodel" in order
        assert "deepseek" in order
        assert len(order) == 7
    workflow = Path(".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    for token in (
        "BIGMODEL_API_KEY", "DEEPSEEK_API_KEY", "BIGMODEL_BASE_URL", "DEEPSEEK_BASE_URL",
        "glm-4.7-flash", "deepseek-v4-flash", "PIF_DEEPSEEK_GRANTED_BALANCE_ONLY",
    ):
        assert token in workflow


def test_chinese_quota_and_rate_errors_are_classified():
    assert classify_llm_failure("账户余额已用完") == "quota_exhausted"
    assert classify_llm_failure("请求过于频繁，并发超额") == "rate_limited"
