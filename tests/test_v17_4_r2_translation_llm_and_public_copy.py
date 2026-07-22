from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pifactory import translation
from pifactory.llm import LLMError, LLMRouter, classify_llm_failure
from pifactory.public_display import sanitize_public_text
from pifactory.render import supplementary_paper_card


ROOT = Path(__file__).resolve().parents[1]


class NeverHTTP:
    def request(self, *args, **kwargs):
        raise AssertionError("network must not be called")

    def get_json(self, *args, **kwargs):
        raise AssertionError("network must not be called")


class PlainTextTranslationLLM:
    available = True

    def provider_order(self, purpose: str):
        assert purpose == "translation"
        return ("mistral",)

    def json_task(self, **kwargs):
        raise LLMError(
            "structured response was invalid",
            category="invalid_json",
            attempts=[{"provider": "mistral", "model": "m", "status": "failed", "failure_category": "invalid_json"}],
        )

    def text_task(self, **kwargs):
        return SimpleNamespace(
            text="研究共纳入13例病例。",
            provider="mistral",
            model="mistral-small-latest",
            attempts=[{"provider": "mistral", "model": "mistral-small-latest", "status": "success"}],
        )


def test_plain_text_llm_rescues_translation_after_structured_failure(monkeypatch):
    monkeypatch.setattr(translation, "_python_translate", lambda _text: (_ for _ in ()).throw(RuntimeError("all translators failed")))
    text, audit = translation.translate_text(
        "The study enrolled 13 cases.",
        profile={"translation_glossary": []},
        llm=PlainTextTranslationLLM(),
        prompt_text="Return JSON.",
        cache={},
        field_kind="body",
    )
    assert text == "研究共纳入13例病例。"
    assert audit["status"] == "passed_llm_plain_text_rescue"
    assert any(row.get("failure_category") == "invalid_json" for row in audit["attempts"])


def test_structured_translation_accepts_common_result_shape(monkeypatch):
    monkeypatch.setattr(translation, "_python_translate", lambda _text: (_ for _ in ()).throw(RuntimeError("all translators failed")))

    class ShapeLLM:
        available = True
        def provider_order(self, purpose): return ("openrouter",)
        def json_task(self, **kwargs):
            return SimpleNamespace(
                data={"result": {"translation": "研究报告了13例病例。"}},
                provider="openrouter", model="free-model", attempts=[{"status": "success"}],
            )

    text, audit = translation.translate_text(
        "The study reported 13 cases.",
        profile={"translation_glossary": []}, llm=ShapeLLM(), prompt_text="JSON", cache={}, field_kind="body",
    )
    assert text == "研究报告了13例病例。"
    assert audit["rescue_mode"] == "structured_json"


def test_public_copy_strips_review_metadiscourse_and_scope_jargon():
    text = sanitize_public_text("审查得出的结论是：该研究报告了马尔堡病毒感染病例。")
    assert text == "该研究报告了马尔堡病毒感染病例。"
    html = supplementary_paper_card({
        "title": "Comparative filovirus ecology",
        "title_zh": "丝状病毒生态比较",
        "display_mode": "supplementary_related",
        "notice_zh": "与目标病原相关的比较或背景资料。",
        "notice_en": "Comparative or background material related to the target pathogen.",
        "supplementary_reason": "biologically_related_non_target_entity",
    })
    assert "相关资料" in html
    for forbidden in ("范围说明", "审查得出的结论是", "证据不足以建立目标病毒", "不生成结构化"):
        assert forbidden not in html


def test_gemini_uses_header_auth_and_not_query_key(monkeypatch):
    class Response:
        def json(self):
            return {
                "candidates": [{"content": {"parts": [{"text": '{"status":"ok"}'}]}, "finishReason": "STOP"}],
                "usageMetadata": {},
            }

    class RecordingHTTP:
        def __init__(self): self.kwargs = None
        def request(self, method, url, **kwargs): self.kwargs = kwargs; return Response()
        def get_json(self, *args, **kwargs): return {"models": []}

    http = RecordingHTTP()
    router = LLMRouter(http, gemini_key="secret")
    data, _, _ = router._gemini_call("gemini-3.5-flash", "json", "{}", 0.0)
    assert data["status"] == "ok"
    assert http.kwargs["headers"]["x-goog-api-key"] == "secret"
    assert "params" not in http.kwargs


def test_openai_compatible_retries_minimal_payload_when_optional_parameters_rejected(monkeypatch):
    class Response:
        def json(self):
            return {"choices": [{"message": {"content": '{"status":"ok"}'}, "finish_reason": "stop"}], "model": "m", "usage": {}}

    class RecordingHTTP:
        def __init__(self): self.payloads = []
        def request(self, method, url, **kwargs):
            self.payloads.append(kwargs["json"])
            if len(self.payloads) == 1:
                raise RuntimeError("HTTP status 400: unsupported parameter response_format")
            return Response()
        def get_json(self, *args, **kwargs): return {"data": []}

    http = RecordingHTTP()
    router = LLMRouter(http, siliconflow_key="key")
    data, _, _ = router._openai_compatible_call("siliconflow", "Qwen/Qwen3-8B", "json", "{}", 0.0)
    assert data["status"] == "ok"
    assert "response_format" in http.payloads[0]
    assert "response_format" not in http.payloads[1]
    assert "enable_thinking" not in http.payloads[1]


def test_provider_failures_are_actionable():
    assert classify_llm_failure(RuntimeError("SSL EOF occurred in violation of protocol")) == "network_error"
    assert classify_llm_failure(RuntimeError("HTTP 404 model not found")) == "model_not_found"
    assert classify_llm_failure(RuntimeError("HTTP 422 unsupported parameter response_format")) == "unsupported_parameter"
    assert classify_llm_failure(RuntimeError("HTTP 403: Model disabled.")) == "model_not_found"


def test_factory_repo_does_not_document_legacy_top_level_command_entry():
    """Factory pytest must remain runnable as a standalone repository.

    The bundle-level system_manager.sh contract is validated by
    validate_v17_4_r2_acceptance.py/validate_bundle.sh, where the complete
    three-repository release root is actually available.  A Factory checkout
    must not guess that ROOT.parent is the bundle root because the supported
    local installation keeps the control plane under
    ~/pathogen-wechat-publisher/releases/current.
    """
    files = [ROOT / "README.md"]
    files.extend(sorted((ROOT / ".github" / "workflows").glob("*.yml")))
    files.extend(sorted((ROOT / ".github" / "workflows").glob("*.yaml")))
    documented = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in files
        if path.is_file()
    )
    assert "GITHUB_COMMANDS_V17_4_R1_ZH.sh" not in documented
    assert "GITHUB_COMMANDS_V17_4_R2_ZH.sh" not in documented


def test_workflow_enables_translation_plain_rescue_and_provider_specific_timeouts():
    workflow = (ROOT / ".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    assert "PIF_TRANSLATION_PLAIN_TEXT_RESCUE" in workflow
    assert "PIF_LLM_BIGMODEL_TIMEOUT" in workflow
    assert "PIF_LLM_DEEPSEEK_TIMEOUT" in workflow
    assert "gemini-3.5-flash" in workflow
    assert "openai/gpt-oss-120b" in workflow
    assert "qwen/qwen3.6-27b" in workflow
    assert "llama-3.1-8b-instant" in workflow
    assert "PIF_DEEPSEEK_GRANTED_BALANCE_ONLY || 'false'" in workflow

@pytest.mark.parametrize(
    ("provider", "model", "expected_key", "expected_thinking_key"),
    [
        ("groq", "openai/gpt-oss-120b", "max_completion_tokens", ""),
        ("siliconflow", "Qwen/Qwen3-8B", "max_tokens", "enable_thinking"),
        ("bigmodel", "glm-4.7-flash", "max_tokens", "thinking"),
        ("deepseek", "deepseek-v4-flash", "max_tokens", "thinking"),
    ],
)
def test_provider_specific_payloads_disable_thinking_and_use_supported_token_field(
    monkeypatch, provider, model, expected_key, expected_thinking_key
):
    class Response:
        def json(self):
            return {"choices": [{"message": {"content": '{"status":"ok"}'}, "finish_reason": "stop"}], "model": model, "usage": {}}

    class RecordingHTTP:
        def __init__(self): self.payload = None
        def request(self, method, url, **kwargs): self.payload = kwargs["json"]; return Response()
        def get_json(self, *args, **kwargs): return {"data": []}

    http = RecordingHTTP()
    router = LLMRouter(http, provider_keys={provider: "key"})
    data, _, _ = router._openai_compatible_call(provider, model, "json", "{}", 0.0)
    assert data["status"] == "ok"
    assert expected_key in http.payload
    if expected_key == "max_completion_tokens":
        assert "max_tokens" not in http.payload
    if expected_thinking_key == "enable_thinking":
        assert http.payload["enable_thinking"] is False
    elif expected_thinking_key == "thinking":
        assert http.payload["thinking"] == {"type": "disabled"}


def test_model_disabled_is_quarantined_without_disabling_provider():
    from pifactory.provider_state import ProviderRuntimeState

    state = ProviderRuntimeState("groq")
    state.mark_failure("blocked-model", "model_not_found")
    assert state.available()
    assert not state.model_available("blocked-model")
    assert state.model_available("fallback-model")
