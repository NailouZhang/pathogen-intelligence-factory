from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from pifactory.llm import LLMError, LLMRouter, summarize_attempt_categories
from pifactory.public_display import build_display_issue
from pifactory.render import supplementary_news_card, supplementary_paper_card


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class DiscoveryHTTP:
    def __init__(self, model_payload, completion_payload=None):
        self.model_payload = model_payload
        self.completion_payload = completion_payload or {
            "choices": [{"message": {"content": '{"status":"ok"}'}, "finish_reason": "stop"}],
            "model": "returned-model",
            "usage": {},
        }
        self.get_calls = []
        self.request_calls = []

    def get_json(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.model_payload

    def request(self, method, url, **kwargs):
        self.request_calls.append((method, url, kwargs))
        return FakeResponse(self.completion_payload)


def test_discovered_model_precedes_stale_explicit_preference(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "removed-model")
    monkeypatch.setenv("PIF_LLM_MAX_MODELS_PER_PROVIDER", "2")
    http = DiscoveryHTTP({
        "data": [
            {"id": "whisper-large-v3", "active": True},
            {"id": "available-small-chat", "active": True},
            {"id": "available-preview-chat", "active": True},
        ]
    })
    router = LLMRouter(http, provider_keys={"groq": "key"})
    models = router.discover_models("groq", refresh=True)
    assert models[0] == "available-small-chat"
    assert "removed-model" in models
    assert models.index("removed-model") > models.index("available-small-chat")
    audit = router.model_discovery_snapshot("groq")
    assert audit["status"] == "discovered"
    assert audit["discovered_count"] == 2


def test_confirmed_explicit_model_remains_preferred(monkeypatch):
    monkeypatch.setenv("MISTRAL_MODEL", "preferred-chat")
    http = DiscoveryHTTP({
        "data": [
            {"id": "other-small", "archived": False, "capabilities": {"completion_chat": True}},
            {"id": "preferred-chat", "archived": False, "capabilities": {"completion_chat": True}},
        ]
    })
    router = LLMRouter(http, provider_keys={"mistral": "key"})
    assert router.discover_models("mistral", refresh=True)[0] == "preferred-chat"


def test_gemini_discovers_only_generate_content_text_models(monkeypatch):
    class GeminiHTTP(DiscoveryHTTP):
        def get_json(self, url, **kwargs):
            self.get_calls.append((url, kwargs))
            return {
                "models": [
                    {"name": "models/gemini-valid-flash", "baseModelId": "gemini-valid-flash", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/text-embedding-004", "baseModelId": "text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
                    {"name": "models/gemini-image-model", "baseModelId": "gemini-image-model", "supportedGenerationMethods": ["generateContent"]},
                ]
            }

    http = GeminiHTTP({})
    router = LLMRouter(http, provider_keys={"gemini": "key"})
    models = router.discover_models("gemini", refresh=True)
    assert models[0] == "gemini-valid-flash"
    assert "text-embedding-004" not in models
    assert "gemini-image-model" not in models
    _, kwargs = http.get_calls[0]
    assert kwargs["headers"]["x-goog-api-key"] == "key"
    assert kwargs["params"]["pageSize"] == 1000


def test_siliconflow_model_list_is_filtered_to_text_chat():
    http = DiscoveryHTTP({"data": [{"id": "Qwen/Qwen3-8B", "type": "text"}]})
    router = LLMRouter(http, provider_keys={"siliconflow": "key"})
    assert router.discover_models("siliconflow", refresh=True)[0] == "Qwen/Qwen3-8B"
    url, kwargs = http.get_calls[0]
    assert url.endswith("/models")
    assert kwargs["params"] == {"type": "text", "sub_type": "chat"}


def test_discovery_failure_keeps_fallback_and_records_error():
    class FailingHTTP:
        def get_json(self, *args, **kwargs):
            raise RuntimeError("HTTP status 404: models endpoint unavailable")
        def request(self, *args, **kwargs):
            raise AssertionError("completion not expected")

    router = LLMRouter(FailingHTTP(), provider_keys={"bigmodel": "key"})
    models = router.discover_models("bigmodel", refresh=True)
    assert "glm-4.7-flash" in models
    audit = router.model_discovery_snapshot("bigmodel")
    assert audit["status"] == "fallback"
    assert audit["failure_category"] == "unknown" or audit["failure_category"] == "model_not_found"
    assert audit["error"]


def test_live_probe_can_bypass_persisted_provider_and_model_cooldown(monkeypatch):
    monkeypatch.setenv("PIF_LLM_MAX_MODELS_PER_PROVIDER", "1")
    http = DiscoveryHTTP({"data": [{"id": "working-model", "active": True}]})
    router = LLMRouter(http, provider_keys={"groq": "key"})
    state = router.states["groq"]
    state.status = "cooldown"
    state.cooldown_until = time.time() + 3600
    state.models["working-model"] = {"status": "cooldown", "cooldown_until": time.time() + 3600}
    result = router.json_task(
        system="Return JSON", prompt='Return {"status":"ok"}', provider_order=("groq",),
        validator=lambda data: (data.get("status") == "ok", "bad status"),
        ignore_runtime_cooldown=True, max_models_per_provider=1,
    )
    assert result.provider == "groq"
    assert result.attempts[-1]["status"] == "success"


def test_only_skipped_attempts_keep_actionable_category():
    attempts = [
        {"status": "skipped", "failure_category": "model_cooldown"},
        {"status": "skipped", "failure_category": "model_cooldown"},
    ]
    assert summarize_attempt_categories(attempts) == "model_cooldown"


def _supplementary_work():
    return {
        "title": "Comparative filovirus ecology",
        "title_original": "Comparative filovirus ecology",
        "title_zh": "丝状病毒生态比较",
        "wechat_title_zh": "丝状病毒生态比较",
        "wechat_title_en": "Comparative filovirus ecology",
        "authors": ["Alice Example", "Bob Example"],
        "wechat_authors": "Alice Example, Bob Example",
        "journal": "Journal of Filovirus Studies",
        "published_date": "2026-07-22",
        "canonical_publication_date": "2026-07-22",
        "doi": "10.1000/example",
        "url": "https://example.org/article",
        "source_ids": {"pmid": "123456"},
        "notice_zh": "相关资料：与目标病原相关的比较或背景资料。",
        "notice_en": "Related material: comparative or background material related to the target pathogen.",
    }


def test_public_issue_drops_scope_notice_fields_and_phrases():
    issue = build_display_issue({"supplementary_papers": [_supplementary_work()]})
    item = issue["supplementary_papers"][0]
    assert "notice_zh" not in item
    assert "notice_en" not in item
    assert "相关资料" not in str(issue)
    assert "Related material" not in str(issue)


def test_wechat_supplementary_card_matches_information_style_without_links():
    html = supplementary_paper_card(_supplementary_work(), wechat=True)
    for expected in (
        "丝状病毒生态比较", "Comparative filovirus ecology", "作者：",
        "Alice Example, Bob Example", "Journal of Filovirus Studies", "2026-07-22",
    ):
        assert expected in html
    for forbidden in ("相关资料", "DOI", "PMID", "PMCID", "来源", "href="):
        assert forbidden not in html


def test_pages_supplementary_card_keeps_source_links_but_not_backend_notice():
    html = supplementary_paper_card(_supplementary_work(), wechat=False)
    assert "相关资料" not in html
    assert "与目标病原相关的比较或背景资料" not in html
    assert "DOI" in html
    assert "PMID" in html
    assert "来源" in html
    assert "href=" in html


def test_supplementary_news_card_has_no_removed_scope_variable_or_copy():
    html = supplementary_news_card({
        "title": "Filovirus ecology update",
        "title_zh": "丝状病毒生态更新",
        "publisher": "Example Publisher",
        "published_date": "2026-07-22",
        "url": "https://example.org/news",
        "notice_zh": "相关资料：与目标病原相关的比较或背景资料。",
    })
    assert "相关资料" not in html
    assert "丝状病毒生态更新" in html


def test_workflow_enables_discovery_without_hard_pinning():
    """Factory tests must only inspect files owned by the Factory repo.

    public_manager.sh belongs to the three-repository release control layer and
    is validated by the bundle acceptance validator.  Reading ROOT.parent here
    breaks both the supported installed layout and a normal single-repository
    GitHub checkout.
    """
    workflow = (ROOT / ".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    assert "PIF_LLM_DISCOVER_MODELS" in workflow
    assert "PIF_LLM_DISCOVERY_MAX_CANDIDATES" in workflow
    assert "GEMINI_MODEL: ${{ vars.GEMINI_MODEL || '' }}" in workflow
    assert "DEEPSEEK_MODEL: ${{ vars.DEEPSEEK_MODEL || '' }}" in workflow
