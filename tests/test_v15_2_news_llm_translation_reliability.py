from __future__ import annotations

import json
from pathlib import Path

import pytest

from pifactory.content import _news_summary_quality, _news_text_quality
from pifactory.llm import LLMError, LLMResult, LLMRouter, classify_llm_failure
from pifactory.news_state import finalize_news_state, mark_source_qualified
from pifactory.overview import _news_fallback
from pifactory.relevance import _call_review_batch
import pifactory.translation as translation


class NoHTTP:
    def request(self, *args, **kwargs):
        raise AssertionError("network must not be called")

    def get_json(self, *args, **kwargs):
        raise AssertionError("network must not be called")


NEWS_FIELDS = {
    "time": "Published on 2026-07-19.",
    "location_and_population": "The notice concerns residents in the affected district.",
    "event": "The ministry confirmed a new hantavirus case.",
    "scale_impact_and_risk": "One confirmed case was reported and investigation continues.",
    "response_status_and_uncertainty": "Contact tracing is under way; exposure remains under investigation.",
}


def test_english_fallback_keeps_qualified_news_display_ready():
    article = {
        "news_id": "n1",
        "title": "Ministry confirms hantavirus case",
        "content_status": "syndicated_summary",
        "content": "The ministry confirmed a hantavirus case and started contact tracing.",
        "analysis": {"brief_en": "The ministry confirmed a hantavirus case and started contact tracing.", "analysis": dict(NEWS_FIELDS)},
        "elements_en": dict(NEWS_FIELDS),
        "translation_audit": {"ready": False},
    }
    mark_source_qualified(article)
    finalize_news_state(article)
    assert article["source_qualified"] is True
    assert article["analysis_ready"] is True
    assert article["translation_complete"] is False
    assert article["translation_status"] == "english_fallback"
    assert article["display_ready"] is True
    assert article["wechat_ready"] is True
    assert article["title_zh"] == article["title"]
    assert article["elements_zh"]["event"] == NEWS_FIELDS["event"]


def test_news_fallback_never_claims_no_news_when_qualified_english_news_exists():
    news = []
    for index in range(2):
        article = {
            "news_id": f"n{index}",
            "title": f"Official hantavirus update {index}",
            "analysis": {"brief_en": f"Official update {index} confirmed a response action.", "analysis": dict(NEWS_FIELDS)},
            "elements_en": dict(NEWS_FIELDS),
        }
        mark_source_qualified(article)
        finalize_news_state(article)
        news.append(article)
    block = _news_fallback({"profile_id": "hantavirus", "display_name_zh": "汉坦病毒", "display_name_en": "Hantavirus"}, news)
    assert set(block["source_ids"]) == {"n0", "n1"}
    assert not any("未获得" in row for row in block["key_findings_zh"])
    assert any("ministry" in row.lower() for row in block["key_findings_zh"])


def test_relevance_review_uses_relevance_order_and_named_tasks():
    class FakeRouter:
        available = True

        def __init__(self):
            self.calls = []
            self.failures = []

        def provider_order(self, purpose):
            assert purpose == "relevance"
            return ("groq", "bigmodel")

        def json_task(self, **kwargs):
            self.calls.append(kwargs)
            return LLMResult(data={"d": [{"id": "x", "c": "A", "p": 0.9, "r": "target"}]}, provider="groq", model="m", attempts=[])

        def record_task_failure(self, *args, **kwargs):
            self.failures.append((args, kwargs))

    router = FakeRouter()
    compact = _call_review_batch(router, {}, "paper", [{"id": "x", "t": "Hantavirus"}], escalated=False)
    escalated = _call_review_batch(router, {}, "paper", [{"id": "x", "t": "Hantavirus"}], escalated=True)
    assert compact["x"]["stage"] == "compact"
    assert escalated["x"]["stage"] == "escalated"
    assert router.calls[0]["provider_order"] == ("groq", "bigmodel")
    assert router.calls[0]["task_name"] == "relevance_compact_review"
    assert router.calls[1]["task_name"] == "relevance_escalated_review"


def test_relevance_llm_failure_is_audited_not_silently_lost():
    class FailingRouter:
        available = True

        def __init__(self):
            self.recorded = []

        def provider_order(self, purpose):
            return ("groq",)

        def json_task(self, **kwargs):
            raise LLMError("all failed", category="authentication_failed", attempts=[{"provider": "groq", "failure_category": "authentication_failed"}])

        def record_task_failure(self, task_name, error, **context):
            self.recorded.append({"task": task_name, "category": error.category, "attempts": error.attempts, "context": context})

    router = FailingRouter()
    assert _call_review_batch(router, {}, "news", [{"id": "n"}], escalated=False) == {}
    assert router.recorded[0]["task"] == "relevance_compact_review"
    assert router.recorded[0]["category"] == "authentication_failed"
    assert router.recorded[0]["attempts"][0]["provider"] == "groq"


def test_explicit_empty_provider_order_does_not_fall_back_to_extract(monkeypatch):
    router = LLMRouter(NoHTTP(), groq_key="configured")
    monkeypatch.setattr(router, "provider_order", lambda purpose: ("groq",))
    with pytest.raises(LLMError) as exc:
        router.json_task(system="x", prompt="{}", provider_order=(), task_name="empty_order_test")
    assert exc.value.category == "no_provider_configured"
    assert "empty provider order" in exc.value.attempts[0]["error"]


def test_replacing_api_key_resets_stale_authentication_state(monkeypatch, tmp_path: Path):
    state_file = tmp_path / "providers.json"
    monkeypatch.setenv("PIF_PROVIDER_STATE_FILE", str(state_file))
    first = LLMRouter(NoHTTP(), groq_key="old-key")
    first.states["groq"].mark_failure("model", "authentication_failed")
    first._persist_states()
    assert first.states["groq"].status == "authentication_failed"

    second = LLMRouter(NoHTTP(), groq_key="new-key")
    assert second.states["groq"].status == "healthy"
    assert second.states["groq"].available()
    assert second.states["groq"].models == {}


def test_429_is_temporary_rate_limit_not_quota_exhaustion():
    assert classify_llm_failure(RuntimeError("HTTP 429 Too Many Requests")) == "rate_limited"
    assert classify_llm_failure(RuntimeError("HTTP 429 quota exceeded; retry later")) == "rate_limited"


def test_short_official_news_can_pass_structural_quality_without_character_floor():
    text = "WHO confirmed hantavirus infection. Investigation continues."
    valid_body, _, body_audit = _news_text_quality(text, "WHO confirms hantavirus infection", official=True)
    valid_summary, summary_audit = _news_summary_quality(text, "WHO confirms hantavirus infection", official=True)
    assert valid_body is True
    assert valid_summary is True
    assert body_audit["official_short_notice_override"] is True
    assert summary_audit["official_short_notice_override"] is True


def test_translation_health_skips_provider_after_repeated_network_failure(monkeypatch):
    monkeypatch.setenv("PIF_TRANSLATION_NETWORK_FAILURE_THRESHOLD", "1")
    calls = {"google": 0, "direct": 0}

    def fail_google(text):
        calls["google"] += 1
        raise translation.requests.ConnectionError("network unavailable")

    def ok_direct(text):
        calls["direct"] += 1
        return "汉坦病毒官方通报"

    monkeypatch.setattr(translation, "_google_deep_chunk", fail_google)
    monkeypatch.setattr(translation, "_google_direct_chunk", ok_direct)
    health = {"providers": {}}
    first = translation._python_translate("Official hantavirus notice", health)
    second = translation._python_translate("Another official hantavirus notice", health)
    assert first[1] == "python_google_direct"
    assert second[1] == "python_google_direct"
    assert calls["google"] == 1
    assert any(row["provider"] == "python_google_translate" and row["status"] == "skipped" for row in second[2])
