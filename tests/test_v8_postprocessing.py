from pathlib import Path

import src.pifactory.translation as translation
from src.pifactory.content import resolve_and_extract_news
from src.pifactory.overview import build_overviews, select_overview_items
from src.pifactory.translation import build_wechat_news_summary, translate_record


class NoLLM:
    available = False


class MustNotCallLLM:
    available = True

    def json_task(self, **kwargs):
        raise AssertionError("LLM should not be called when Python translation succeeds")


class Response:
    def __init__(self, html: str, url: str = "https://example.org/story"):
        self.text = html
        self.url = url
        self.headers = {"Content-Type": "text/html; charset=utf-8"}


class FakeHTTP:
    def __init__(self, html: str):
        self.html = html

    def request(self, *args, **kwargs):
        return Response(self.html)


def test_overview_selection_is_capped_at_25_and_source_diverse():
    items = [
        {"paper_id": f"p{i}", "journal": "J1" if i < 10 else f"J{i}", "quality_score": 100 - i}
        for i in range(40)
    ]
    selected = select_overview_items(items, minimum=15, maximum=25)
    assert len(selected) == 25
    assert len({x["paper_id"] for x in selected}) == 25


def test_separate_overview_blocks_never_mix_news_and_papers(tmp_path: Path):
    papers = [
        {
            "paper_id": f"p{i}",
            "paper_type": "research",
            "title": f"Paper {i}",
            "title_zh": f"文献{i}",
            "authors": ["A"],
            "journal": "Journal",
            "abstract": "Hantavirus study abstract with evidence.",
            "analysis": {"analysis": {"main_results": "Hantavirus was detected in the supplied evidence."}},
        }
        for i in range(20)
    ]
    news = [
        {
            "news_id": f"n{i}",
            "title": f"News {i}",
            "title_zh": f"新闻{i}",
            "publisher": "Health agency",
            "content_status": "full",
            "content_zh": "卫生机构发布了经正文核验的事件信息。",
            "analysis": {"analysis": {"event": "The agency reported a body-verified event.", "time": "Today"}},
        }
        for i in range(18)
    ]
    result = build_overviews(
        {"profile_id": "hantavirus", "display_name_zh": "汉坦病毒", "display_name_en": "Hantavirus"},
        papers,
        news,
        NoLLM(),
        tmp_path,
        minimum=15,
        maximum=25,
    )
    assert result["literature"]["input_count"] == 20
    assert result["news"]["input_count"] == 18
    assert "文献" in result["literature"]["headline_zh"]
    assert "新闻" in result["news"]["headline_zh"]


def test_news_extractor_accepts_body_and_rejects_title_only():
    body = " ".join(
        [
            "The regional health authority confirmed a suspected hantavirus case in County A on Tuesday.",
            "The patient remained stable while confirmatory testing and exposure investigation continued.",
            "Officials advised residents to avoid contact with rodent urine and droppings and to ventilate enclosed buildings.",
            "No additional cases had been reported at the time of publication, and the route of exposure remained under investigation.",
        ]
        * 4
    )
    html = f"<html><head><title>Health authority report</title></head><body><article><h1>Health authority report</h1><p>{body}</p></article></body></html>"
    record = resolve_and_extract_news(FakeHTTP(html), {"title": "Health authority report", "url": "https://example.org/story", "excerpt": "Health authority report"})
    assert record["content_status"] in {"full", "partial"}
    assert len(record["content"]) > 500
    assert record["content"] != record["title"]

    title_html = "<html><body><article><p>Health authority report</p></article></body></html>"
    rejected = resolve_and_extract_news(FakeHTTP(title_html), {"title": "Health authority report", "url": "https://example.org/title", "excerpt": "Health authority report"})
    assert rejected["content_status"] in {"title_only_rejected", "excerpt_only", "unavailable"}
    assert not rejected["content"]


def test_python_translation_runs_before_llm(monkeypatch, tmp_path: Path):
    mapping = {
        "English title": "英文标题",
        "Abstract with 13 cases.": "包含13例病例的摘要。",
        "Question": "研究问题",
        "Design": "研究设计",
        "Methods": "研究方法",
        "13 cases": "13例病例",
        "Interpretation": "研究解释",
        "Significance": "研究意义",
        "Limitations": "研究局限",
    }

    def fake_python(text: str):
        return mapping[text], "python_google_translate", [{"provider": "python_google_translate", "status": "success"}]

    monkeypatch.setattr(translation, "_python_translate", fake_python)
    (tmp_path / "translate_zh.md").write_text("Return JSON.", encoding="utf-8")
    record = {
        "title": "English title",
        "abstract": "Abstract with 13 cases.",
        "analysis": {
            "analysis": {
                "research_question_and_background": "Question",
                "study_design_and_population": "Design",
                "methods": "Methods",
                "main_results": "13 cases",
                "interpretation_and_novelty": "Interpretation",
                "scientific_and_public_health_significance": "Significance",
                "limitations_and_evidence_strength": "Limitations",
            }
        },
    }
    translate_record(record, profile={"translation_glossary": []}, llm=MustNotCallLLM(), prompts_dir=tmp_path, cache={}, kind="research")
    assert record["translation_ready"] is True
    assert record["translation_audit"]["title"]["status"] == "passed_python"


def test_wechat_news_summary_never_exceeds_500_chinese_characters():
    fields = {
        "time": "2026年7月18日。" * 20,
        "location_and_population": "某地区居民和接触者。" * 30,
        "event": "卫生部门确认并调查了一起与汉坦病毒有关的事件。" * 30,
        "scale_impact_and_risk": "病例规模和传播风险仍在评估。" * 30,
        "response_status_and_uncertainty": "正在开展检测、追踪和风险沟通，部分信息尚待确认。" * 30,
    }
    result = build_wechat_news_summary(fields, "", limit=500)
    assert result
    assert len(result) <= 500
    for label in ("时间：", "地点与对象：", "事件："):
        assert label in result


def test_llm_is_used_only_after_all_python_translation_routes_fail(monkeypatch):
    from types import SimpleNamespace

    def fail_python(_text: str):
        raise RuntimeError("all free Python translators failed")

    class FinalFallbackLLM:
        available = True

        def json_task(self, **kwargs):
            return SimpleNamespace(
                data={"translation_zh": "研究共报告13例病例。"},
                provider="gemini",
                model="test-model",
                attempts=[{"provider": "gemini", "status": "success"}],
            )

    monkeypatch.setattr(translation, "_python_translate", fail_python)
    result, audit = translation.translate_text(
        "The study reported 13 cases.",
        profile={"translation_glossary": []},
        llm=FinalFallbackLLM(),
        prompt_text="Return JSON.",
        cache={},
        field_kind="body",
    )
    assert result == "研究共报告13例病例。"
    assert audit["status"] == "passed_llm_final_fallback"
    assert audit["provider"] == "gemini"
