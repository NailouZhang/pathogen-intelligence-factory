from __future__ import annotations

import json
from pathlib import Path

from pifactory.render import news_card, render_site, render_wechat_package, visible_text_count


def _paper(index: int, *, detail: str = "经核验内容") -> dict:
    return {
        "paper_id": f"paper-{index}",
        "title": f"English paper title {index}",
        "title_zh": f"中文文献标题 {index}",
        "journal": "Journal",
        "canonical_publication_date": "2026-07-20",
        "paper_type": "research",
        "priority_tier": "A" if index < 10 else "B",
        "authors": ["Author A", "Author B"],
        "abstract_zh": detail,
        "elements_zh": {
            "research_question_and_background": detail,
            "study_design_and_population": detail,
            "methods": detail,
            "main_results": detail,
            "interpretation_and_novelty": detail,
            "scientific_and_public_health_significance": detail,
            "limitations_and_evidence_strength": detail,
        },
    }


def _supplementary_paper(index: int, *, title_size: int = 180) -> dict:
    return {
        "paper_id": f"supp-{index}",
        "title": f"Supplementary English title {index} " + ("E" * title_size),
        "title_zh": f"补充文献中文标题 {index} " + ("中" * title_size),
        "journal": "Supplementary Journal",
        "canonical_publication_date": "2026-07-20",
    }


def _news(index: int, *, size: int = 80) -> dict:
    text = f"新闻内容 {index} " + ("新" * size)
    return {
        "news_id": f"news-{index}",
        "title": f"English news title {index}",
        "title_zh": f"中文新闻标题 {index}",
        "published_date": "2026-07-20",
        "publisher": "Authority",
        "priority_tier": "B",
        "content_zh": text * 3,
        "wechat_summary_zh": f"微信简报 {index}",
        "elements_zh": {
            "time": text,
            "location_and_population": text,
            "event": text,
            "scale_impact_and_risk": text,
            "response_status_and_uncertainty": text,
        },
    }


def _supplementary_news(index: int, *, size: int = 300) -> dict:
    return {
        "news_id": f"supp-news-{index}",
        "title": f"Supplementary news {index}",
        "title_zh": f"补充新闻 {index}",
        "published_date": "2026-07-20",
        "publisher": "Authority",
        "excerpt": "简讯" * size,
    }


def _issue(*, papers: list[dict], supplementary_papers: list[dict], news: list[dict], supplementary_news: list[dict]) -> dict:
    return {
        "schema_version": "6.2",
        "issue_id": "sars_cov_2-2026-07-20",
        "profile_id": "sars_cov_2",
        "issue_date": "2026-07-20",
        "generated_at": "2026-07-20T00:00:00Z",
        "window_start": "2026-07-14",
        "window_end": "2026-07-20",
        "title_zh": "新冠病毒每周情报",
        "title_en": "SARS-CoV-2 Weekly Intelligence",
        "papers": papers,
        "supplementary_papers": supplementary_papers,
        "news": news,
        "supplementary_news": supplementary_news,
        "overview": {"literature": {}, "news": {}},
        "metrics": {"translated": len(papers)},
        "retrieval_funnel": {
            "papers": {"primary_displayed": len(papers), "supplementary_displayed": len(supplementary_papers)},
            "news": {"displayed": len(news), "supplementary_displayed": len(supplementary_news)},
        },
    }


def _cover() -> dict:
    return {"cover_sha256": "abc", "generator": "test", "profile_fingerprint": "fp"}


def test_wechat_prefers_compact_news_summary_and_enforces_limit(monkeypatch) -> None:
    monkeypatch.setenv("PIF_WECHAT_NEWS_MAX_ZH_CHARS", "500")
    article = _news(1, size=20)
    article["content_zh"] = "仅存在于完整网页的超长新闻正文" * 100
    article["wechat_summary_zh"] = "这是微信专用短简报。"
    html = news_card(article, wechat=True)
    assert "这是微信专用短简报。" in html
    assert "仅存在于完整网页的超长新闻正文" not in html
    page_html = news_card(article, wechat=False)
    assert "仅存在于完整网页的超长新闻正文" in page_html


def test_wechat_omits_bottom_supplementary_papers_and_discloses_count(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PIF_WECHAT_MAX_VISIBLE_CHARS", "9000")
    monkeypatch.setenv("PIF_WECHAT_MIN_FULL_PAPERS", "10")
    monkeypatch.setenv("PIF_WECHAT_MIN_SUPPLEMENTARY_PAPERS", "0")
    issue = _issue(
        papers=[_paper(i) for i in range(10)],
        supplementary_papers=[_supplementary_paper(i) for i in range(100)],
        news=[],
        supplementary_news=[],
    )
    audit = render_wechat_package(issue, tmp_path, _cover())
    html = (tmp_path / "wechat-package/article.html").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "wechat-package/manifest.json").read_text(encoding="utf-8"))

    assert audit["policy_version"] == "v17-wechat-visible-text-budget-audit-only-1"
    assert audit["within_budget"] is True
    assert audit["supplementary_papers_total"] == 100
    assert audit["supplementary_papers_omitted"] > 0
    assert audit["supplementary_papers_displayed"] + audit["supplementary_papers_omitted"] == 100
    assert "微信公众号篇幅说明" not in html
    assert "未在正文展开" not in html
    assert audit["operational_notice_rendered"] is False
    assert visible_text_count(html) <= 9000
    assert manifest["source"]["supplementary_papers"] == 100
    assert manifest["source"]["supplementary_papers_omitted_wechat"] == audit["supplementary_papers_omitted"]

    render_site(issue, tmp_path)
    page = (tmp_path / "site/index.html").read_text(encoding="utf-8")
    assert "补充文献中文标题 99" in page
    assert "补充文献中文标题 0" in page


def test_budget_order_removes_supplementary_news_excerpts_before_paper_cards(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PIF_WECHAT_MAX_VISIBLE_CHARS", "7200")
    monkeypatch.setenv("PIF_WECHAT_MIN_FULL_PAPERS", "10")
    issue = _issue(
        papers=[_paper(i) for i in range(10)],
        supplementary_papers=[_supplementary_paper(i, title_size=120) for i in range(30)],
        news=[],
        supplementary_news=[_supplementary_news(i, size=500) for i in range(12)],
    )
    audit = render_wechat_package(issue, tmp_path, _cover())
    actions = [step["action"] for step in audit["compaction_steps"]]
    assert "remove_supplementary_news_excerpt" in actions
    assert "omit_supplementary_paper_card" in actions
    assert actions.index("remove_supplementary_news_excerpt") < actions.index("omit_supplementary_paper_card")
    assert audit["removed_supplementary_news_excerpts"] > 0
    assert audit["supplementary_papers_omitted"] > 0


def test_emergency_main_news_omission_is_audited_and_disclosed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PIF_WECHAT_MAX_VISIBLE_CHARS", "10500")
    monkeypatch.setenv("PIF_WECHAT_MIN_FULL_PAPERS", "10")
    monkeypatch.setenv("PIF_WECHAT_MIN_MAIN_NEWS", "10")
    issue = _issue(
        papers=[_paper(i) for i in range(10)],
        supplementary_papers=[],
        news=[_news(i, size=80) for i in range(30)],
        supplementary_news=[],
    )
    audit = render_wechat_package(issue, tmp_path, _cover())
    html = (tmp_path / "wechat-package/article.html").read_text(encoding="utf-8")
    assert audit["main_news_omitted"] > 0
    assert audit["main_news_displayed"] >= 10
    assert audit["main_news_displayed"] + audit["main_news_omitted"] == 30
    assert "极端篇幅兜底" not in html
    assert audit["operational_notice_rendered"] is False
    assert visible_text_count(html) <= 10500


def test_oversized_protected_content_uses_field_limits_and_can_omit_bottom_primary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PIF_WECHAT_MAX_VISIBLE_CHARS", "18000")
    monkeypatch.setenv("PIF_WECHAT_MIN_FULL_PAPERS", "10")
    monkeypatch.setenv("PIF_WECHAT_MIN_PRIMARY_PAPERS", "10")
    issue = _issue(
        papers=[_paper(i, detail="超长结构化内容" * 300) for i in range(30)],
        supplementary_papers=[],
        news=[],
        supplementary_news=[],
    )
    audit = render_wechat_package(issue, tmp_path, _cover())
    html = (tmp_path / "wechat-package/article.html").read_text(encoding="utf-8")
    assert audit["within_budget"] is True
    assert sum(audit["truncated_display_fields"].values()) > 0
    assert audit["primary_papers_displayed"] >= 10
    assert audit["primary_papers_displayed"] + audit["primary_papers_omitted"] == 30
    assert visible_text_count(html) <= 18000
    assert "微信公众号篇幅说明" not in html
    assert audit["operational_notice_rendered"] is False
