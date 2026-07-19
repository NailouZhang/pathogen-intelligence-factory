from __future__ import annotations

from pifactory.llm import LLMRouter, OPENAI_COMPATIBLE_PROVIDERS


class DummyResponse:
    def json(self):
        return {
            "model": "Qwen/Qwen3-8B",
            "choices": [{"message": {"content": '{"status":"ok"}'}}],
            "usage": {},
        }


class RecordingHTTP:
    def __init__(self):
        self.requests = []
        self.gets = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return DummyResponse()

    def get_json(self, url, **kwargs):
        self.gets.append((url, kwargs))
        if url.endswith('/user/info'):
            return {"status": True, "data": {"status": "normal", "balance": "1"}}
        return {"object": "list", "data": [{"id": "Qwen/Qwen3-8B"}]}


def test_siliconflow_defaults_to_china_endpoint(monkeypatch):
    monkeypatch.delenv("SILICONFLOW_BASE_URL", raising=False)
    router = LLMRouter(RecordingHTTP(), siliconflow_key="sf-test")
    assert OPENAI_COMPATIBLE_PROVIDERS["siliconflow"]["base_url"] == "https://api.siliconflow.cn/v1"
    assert router.provider_base_url("siliconflow") == "https://api.siliconflow.cn/v1"


def test_siliconflow_chat_models_and_account_share_cn_base(monkeypatch):
    monkeypatch.delenv("SILICONFLOW_BASE_URL", raising=False)
    http = RecordingHTTP()
    router = LLMRouter(http, siliconflow_key="sf-test")
    router._openai_compatible_call(
        "siliconflow", "Qwen/Qwen3-8B", "Return JSON", "{}", 0.0
    )
    router._discover_models("siliconflow")
    router.provider_account_info("siliconflow")
    assert http.requests[0][1] == "https://api.siliconflow.cn/v1/chat/completions"
    assert any(url == "https://api.siliconflow.cn/v1/models" for url, _ in http.gets)
    assert any(url == "https://api.siliconflow.cn/v1/user/info" for url, _ in http.gets)


def test_siliconflow_base_url_override_is_normalized(monkeypatch):
    monkeypatch.setenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1/")
    router = LLMRouter(RecordingHTTP(), siliconflow_key="sf-test")
    assert router.provider_base_url("siliconflow") == "https://api.siliconflow.cn/v1"
