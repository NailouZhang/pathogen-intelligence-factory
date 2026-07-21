from __future__ import annotations

from pathlib import Path

from pifactory.public_display import build_display_issue
from pifactory.render import _overview_html, render_site, render_wechat_package

BANNED = (
    "完整资格清单", "Top50表示进入深度主报告", "微信公众号篇幅说明",
    "证据边界", "translation_status=english_fallback", "未在正文展开",
    "本期重点文献按发表日期", "部分证据来自摘要、观察性研究",
    "本期新闻资格由来源、日期、正文身份", "中文字段不可用时",
    "中文翻译不完整时会以英文证据",
)


def _issue() -> dict:
    return {
        "issue_id": "hantavirus-2026-07-21",
        "profile_id": "hantavirus",
        "issue_date": "2026-07-21",
        "generated_at": "2026-07-21T00:00:00Z",
        "window_start": "2026-07-15",
        "window_end": "2026-07-21",
        "title_zh": "汉坦病毒情报",
        "title_en": "Hantavirus Intelligence",
        "papers": [], "supplementary_papers": [], "news": [], "supplementary_news": [],
        "overview": {
            "literature": {
                "headline_zh": "本期文献进展", "headline_en": "Literature Brief",
                "brief_items_zh": ["监测研究报告了新的宿主暴露证据。", "临床研究补充了患者结局信息。", "分子研究更新了病毒变异线索。"],
                "brief_items_en": ["Surveillance studies reported new host-exposure evidence.", "Clinical studies added patient-outcome evidence.", "Molecular studies updated viral-variation evidence."],
                "caveats_zh": "证据边界:部分证据来自摘要。",
                "policy_notice": "本期重点文献按发表日期、相关性、证据等级和研究质量综合排序。",
            },
            "news": {
                "policy_notice": "本期新闻资格由来源、日期、正文身份和相关性终审决定；中文字段不可用时使用英文。",
                "translation_notice": "中文翻译不完整时会以英文证据填充中文显示位置。",
            },
        },
        "retrieval_funnel": {"papers": {}, "news": {}},
        "metrics": {},
        "wechat_budget_notice": "微信公众号篇幅说明：未在正文展开100篇。",
        "analysis_quality": {"message": "完整资格清单不因公众号字符上限改变。"},
        "backend_note": "部分证据来自摘要、观察性研究或叙述性综述，结论应结合研究设计和证据等级理解。",
    }


def test_public_sanitizer_removes_operational_text_but_keeps_content() -> None:
    cleaned = build_display_issue(_issue())
    text = str(cleaned)
    assert "监测研究报告了新的宿主暴露证据" in text
    for phrase in BANNED:
        assert phrase not in text


def test_brief_is_rendered_as_flat_bullet_items() -> None:
    block = _overview_html((_issue()["overview"])["literature"], "文献进展")
    assert block.count("<li>") == 6
    assert "<ul" in block
    assert "<ol" not in block


def test_pages_and_wechat_never_render_backend_notices(tmp_path: Path) -> None:
    issue = _issue()
    render_site(issue, tmp_path)
    render_wechat_package(issue, tmp_path, {"cover_sha256": "x", "generator": "test", "profile_fingerprint": "x"})
    contents = [
        (tmp_path / "site/index.html").read_text(encoding="utf-8"),
        (tmp_path / "wechat-package/article.html").read_text(encoding="utf-8"),
    ]
    for content in contents:
        for phrase in BANNED:
            assert phrase not in content
