from pathlib import Path

import pifactory.translation as translation
from pifactory.translation import _looks_chinese, _repair_zh, translate_record


PROFILE = {
    "translation_glossary": [
        {"source": "Orthohantavirus", "target": "正汉坦病毒"},
        {"source": "hantavirus", "target": "汉坦病毒"},
        {"source": "Andes virus", "target": "安第斯病毒"},
    ]
}


class NoLLM:
    available = False


def test_plural_glossary_repair_removes_english_suffix():
    repaired = _repair_zh("Orthohantaviruses and Hantaviruses are important pathogens.", PROFILE["translation_glossary"])
    assert "正汉坦病毒es" not in repaired
    assert "汉坦病毒es" not in repaired
    assert "正汉坦病毒" in repaired
    assert "汉坦病毒" in repaired


def test_technical_title_validation_allows_scientific_abbreviations():
    title = "HFRS患者CASP1和IL-6基因多态性分析"
    assert _looks_chinese(title, "Analysis of CASP1 and IL-6 polymorphisms in HFRS patients", "title")[0]


def test_record_card_body_uses_abstract_translation_not_structured_analysis(monkeypatch, tmp_path: Path):
    def fake_python_translate(text: str):
        mapping = {
            "An English title": "一个英文标题",
            "This is the original abstract with 13 cases.": "这是包含13例病例的原始摘要。",
            "research context": "研究问题与背景",
            "cross-sectional study in workers": "林业工作者横断面研究",
            "antibody testing": "抗体检测方法",
            "13 cases": "共13例病例",
            "novel occupational evidence": "新的职业暴露证据",
            "surveillance significance": "监测意义",
            "abstract only": "仅有摘要，证据有限",
        }
        return mapping[text], "python_google_translate", [{"provider": "python_google_translate", "status": "success"}]

    monkeypatch.setattr(translation, "_python_translate", fake_python_translate)
    prompt = tmp_path / "translate_zh.md"
    prompt.write_text("Translate faithfully and return JSON.", encoding="utf-8")
    record = {
        "title": "An English title",
        "abstract": "This is the original abstract with 13 cases.",
        "analysis": {
            "analysis": {
                "research_question_and_background": "research context",
                "study_design_and_population": "cross-sectional study in workers",
                "methods": "antibody testing",
                "main_results": "13 cases",
                "interpretation_and_novelty": "novel occupational evidence",
                "scientific_and_public_health_significance": "surveillance significance",
                "limitations_and_evidence_strength": "abstract only",
            }
        },
    }
    translate_record(record, profile=PROFILE, llm=NoLLM(), prompts_dir=tmp_path, cache={}, kind="research")
    assert record["title_zh"] == "一个英文标题"
    assert record["abstract_zh"] == "这是包含13例病例的原始摘要。"
    assert record["summary_zh"] == record["abstract_zh"]
    assert record["analysis_zh"]["research_question_and_background"] == "研究问题与背景"
    assert record["translation_ready"] is True
    assert "问题与背景：" not in record["summary_zh"]


def test_direct_google_endpoint_payload(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [[['汉坦病毒研究', 'Hantavirus research', None, None]]]

    monkeypatch.setattr(translation.requests, "get", lambda *args, **kwargs: Response())
    assert translation._google_direct_chunk("Hantavirus research") == "汉坦病毒研究"


def test_placeholder_is_not_a_real_translation_metric():
    from pifactory.utils import clean_space

    def real(item):
        title = clean_space(item.get("title_zh"))
        if not title or "翻译暂不可用" in title or "中文标题暂不可用" in title:
            return False
        status = clean_space(((item.get("translation_audit") or {}).get("title") or {}).get("status"))
        return status not in {"translation_unavailable", "empty_source"}

    assert not real({"title_zh": "中文标题翻译暂不可用", "translation_audit": {"title": {"status": "translation_unavailable"}}})
    assert real({"title_zh": "汉坦病毒研究", "translation_audit": {"title": {"status": "passed_python_fallback"}}})
