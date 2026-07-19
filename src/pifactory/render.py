from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .utils import clean_space, dump_json, html_escape

COLORS = {
    "navy": "#2c3e50",
    "paper_green": "#27ae60",
    "paper_green_dark": "#1e7e34",
    "paper_green_bg": "#f0fff4",
    "news_red": "#c53030",
    "news_red_bg": "#fff5f5",
    "amber": "#c05621",
    "amber_bg": "#fffcf0",
    "amber_line": "#fbd38d",
    "line": "#e2e8f0",
    "panel": "#f8fafc",
    "muted": "#718096",
}

SITE_CSS = """
:root{--navy:#2c3e50;--green:#27ae60;--red:#c53030;--amber:#c05621;--line:#e2e8f0;--muted:#718096}
*{box-sizing:border-box}body{margin:0;background:#f4f7f9;color:#333;font-family:Arial,'Noto Sans CJK SC',sans-serif}a{color:#0366d6;text-decoration:none}.page{max-width:1040px;margin:24px auto;background:#fff;border-radius:15px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.1)}.hero{background:var(--navy);color:#fff;text-align:center}.hero img{width:100%;display:block;max-height:442px;object-fit:cover}.hero-text{padding:20px 30px 26px}.hero h1{margin:0;font-size:30px}.hero p{margin:8px 0 0;opacity:.82}.overview{padding:25px;background:#fffcf0;border-bottom:5px solid #fbd38d}.overview h2{color:var(--amber);margin:0 0 12px;font-size:19px}.overview p{margin:7px 0;line-height:1.75}.overview-statline{padding:14px 25px;background:#edf2f7;border-bottom:1px solid var(--line);font-size:14px;line-height:1.75;font-weight:700;color:#2d3748}.overview-statline p{margin:3px 0}.statistics-note{padding:10px 25px;background:#f8fafc;border-bottom:1px solid var(--line);font-size:12px;line-height:1.65;color:#586069}.toolbar{display:flex;justify-content:flex-end;gap:8px;padding:12px 30px;border-bottom:1px solid var(--line)}button{font:inherit;border:1px solid var(--line);background:#fff;padding:6px 10px;cursor:pointer}.stats{display:grid;grid-template-columns:repeat(5,1fr);border-bottom:1px solid var(--line)}.stats div{padding:16px;text-align:center;border-right:1px solid var(--line)}.stats div:last-child{border-right:0}.stats strong{font-size:27px;color:var(--red);display:block}.content{padding:30px}.content section{margin-top:34px}.content section:first-child{margin-top:0}.section-title{font-size:22px;padding-left:15px;margin:0 0 18px;border-left:6px solid}.section-title.research,.section-title.review{color:var(--green);border-color:var(--green)}.section-title.supplementary{color:#4a5568;border-color:#a0aec0}.section-title.news{color:var(--red);border-color:var(--red)}.card{margin-bottom:28px;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#fff}.supplementary-card{border-style:dashed;background:#fbfdff}.meta-strip{padding:10px 15px;background:#f8fafc;border-bottom:1px solid var(--line);font-size:12px;color:#666;line-height:1.65}.card-body{padding:20px}.card h3{font-size:19px;color:#1a365d;line-height:1.45;margin:0}.title-en{font-size:14px;color:var(--muted);font-style:italic;margin-top:6px;line-height:1.5}.authors{font-size:13px;color:#586069;margin:10px 0}.translated-body{font-size:15px;line-height:1.75;margin:15px 0;padding:15px;border-radius:6px;background:#f0fff4}.news .translated-body{background:#fff5f5}.translated-body strong{display:block;margin-bottom:6px;color:#1e7e34}.news .translated-body strong{color:var(--red)}.supplementary-notice{font-size:13px;line-height:1.65;color:#586069;background:#edf2f7;padding:10px 12px;border-radius:6px;margin-top:12px}details{margin-top:12px;border-top:1px dotted var(--line);border-bottom:1px dotted var(--line);padding:9px 0}summary{cursor:pointer;font-weight:700;color:var(--amber)}.five-grid{display:grid;grid-template-columns:88px 1fr;gap:7px 10px;margin-top:10px;font-size:14px;line-height:1.55}.five-grid dt{font-weight:700;color:var(--amber)}.five-grid dd{margin:0}.original{font-size:13px;line-height:1.65;color:#666;background:#f8fafc;padding:12px;margin-top:10px;border-radius:6px}.links{text-align:right;font-size:13px;margin-top:14px;font-weight:700}.tier-badge{display:inline-block;padding:2px 7px;border-radius:999px;font-size:11px;font-weight:700;margin-right:6px}.tier-A{background:#e6fffa;color:#06735f}.tier-B{background:#ebf8ff;color:#2b6cb0}.tier-C{background:#f7fafc;color:#718096;border:1px solid #e2e8f0}footer{background:var(--navy);color:#fff;padding:20px;text-align:center;font-size:11px;line-height:1.6}[hidden]{display:none!important}@media(max-width:700px){.page{margin:0;border-radius:0}.content{padding:18px}.stats{grid-template-columns:repeat(2,1fr)}.five-grid{grid-template-columns:70px 1fr}.toolbar{justify-content:center;padding:10px}}
"""

SITE_JS = r"""
document.querySelectorAll('[data-language]').forEach(button=>button.addEventListener('click',()=>{
 const lang=button.dataset.language;
 document.querySelectorAll('.lang-zh').forEach(x=>x.hidden=lang!=='zh');
 document.querySelectorAll('.lang-en').forEach(x=>x.hidden=lang!=='en');
}));
"""


def _date_label(label: str, value: Any) -> str | None:
    value = clean_space(value)
    return f"<strong>{html_escape(label)}:</strong> {html_escape(value)}" if value else None


def _paper_meta(work: dict[str, Any]) -> str:
    status_labels = {
        "in_window": "Current window",
        "in_window_month_precision": "Current window (month precision)",
        "future_scheduled": "Future scheduled publication",
    }
    items = [
        _date_label("Journal", work.get("journal") or "Unknown Journal"),
        _date_label("Canonical publication date", work.get("canonical_publication_date") or work.get("availability_date")),
        _date_label("Date basis", work.get("canonical_publication_date_basis") or work.get("availability_date_basis")),
        _date_label("Publication status", status_labels.get(work.get("publication_date_status"), work.get("publication_date_status"))),
        _date_label("DOI", work.get("doi")),
    ]
    citation = ", ".join(str(x) for x in [work.get("year"), work.get("volume"), work.get("issue"), work.get("pages")] if x not in (None, ""))
    if citation:
        items.append(_date_label("Citation", citation))
    return " &nbsp;|&nbsp; ".join(x for x in items if x)


def _paper_fields(kind: str) -> list[tuple[str, str]]:
    if kind == "review":
        return [
            ("范围与问题", "scope_and_question"),
            ("证据基础与方法", "evidence_base_and_review_method"),
            ("共识与结论", "consensus_and_key_conclusions"),
            ("争议与缺口", "controversies_and_evidence_gaps"),
            ("科研与实践启示", "research_and_practice_implications"),
        ]
    return [
        ("问题与背景", "research_question_and_background"),
        ("设计与对象", "study_design_and_population"),
        ("核心方法", "methods"),
        ("主要结果", "main_results"),
        ("解释与创新", "interpretation_and_novelty"),
        ("科研与公卫意义", "scientific_and_public_health_significance"),
        ("局限与证据强度", "limitations_and_evidence_strength"),
    ]


def _paper_fields_en(kind: str) -> list[tuple[str, str]]:
    if kind == "review":
        return [
            ("Scope and question", "scope_and_question"),
            ("Evidence base and method", "evidence_base_and_review_method"),
            ("Consensus and conclusions", "consensus_and_key_conclusions"),
            ("Controversies and gaps", "controversies_and_evidence_gaps"),
            ("Research and practice implications", "research_and_practice_implications"),
        ]
    return [
        ("Question and background", "research_question_and_background"),
        ("Design and population", "study_design_and_population"),
        ("Core methods", "methods"),
        ("Main results", "main_results"),
        ("Interpretation and novelty", "interpretation_and_novelty"),
        ("Scientific and public-health significance", "scientific_and_public_health_significance"),
        ("Limitations and evidence strength", "limitations_and_evidence_strength"),
    ]


def _news_fields() -> list[tuple[str, str]]:
    return [
        ("时间", "time"),
        ("地点与对象", "location_and_population"),
        ("事件", "event"),
        ("规模、影响与风险", "scale_impact_and_risk"),
        ("应对、状态与不确定性", "response_status_and_uncertainty"),
    ]


def _news_fields_en() -> list[tuple[str, str]]:
    return [
        ("Time", "time"),
        ("Location and population", "location_and_population"),
        ("Event", "event"),
        ("Scale, impact and risk", "scale_impact_and_risk"),
        ("Response, status and uncertainty", "response_status_and_uncertainty"),
    ]


def _five_elements(data: dict[str, Any], fields: list[tuple[str, str]], missing_text: str = "原始证据未报告") -> str:
    return "".join(f"<dt>{html_escape(label)}</dt><dd>{html_escape(data.get(key) or missing_text)}</dd>" for label, key in fields)


def _tier_badge(item: dict[str, Any], *, wechat: bool = False) -> str:
    tier = str(item.get("priority_tier") or "C").upper()
    label = {"A": "A 高优先级", "B": "B 常规重要", "C": "C 补充信息"}.get(tier, "C 补充信息")
    title = html_escape(item.get("priority_tier_reason") or "")
    if wechat:
        bg = {"A": "#e6fffa", "B": "#ebf8ff", "C": "#f7fafc"}.get(tier, "#f7fafc")
        fg = {"A": "#06735f", "B": "#2b6cb0", "C": "#718096"}.get(tier, "#718096")
        return f'<span title="{title}" style="display:inline-block;padding:2px 7px;border-radius:999px;background:{bg};color:{fg};font-size:11px;font-weight:700;margin-right:6px;">{label}</span>'
    return f'<span class="tier-badge tier-{html_escape(tier)}" title="{title}">{label}</span>'


def _tier_badge_en(item: dict[str, Any]) -> str:
    tier = str(item.get("priority_tier") or "C").upper()
    label = {"A": "A high priority", "B": "B routine priority", "C": "C supplementary"}.get(tier, "C supplementary")
    return f'<span class="tier-badge tier-{html_escape(tier)}">{label}</span>'


def _analysis_quality_banner(issue: dict[str, Any], *, wechat: bool = False) -> str:
    """Kept as a compatibility helper; v15 never renders this publicly."""
    return ""


def paper_card(work: dict[str, Any], *, wechat: bool = False) -> str:
    kind = work.get("paper_type") or "research"
    title_en = clean_space(work.get("title")) or "Untitled"
    title_zh = clean_space(work.get("title_zh")) or title_en
    abstract_zh = clean_space(work.get("abstract_zh") or work.get("summary_zh"))
    original = clean_space(work.get("abstract") or work.get("full_text_excerpt"))
    authors = ", ".join((work.get("authors") or [])[:10]) or "Authors unavailable"
    elements_zh = work.get("elements_zh") or work.get("analysis_zh") or {}
    elements_en = work.get("elements_en") or work.get("analysis_en") or ((work.get("analysis") or {}).get("analysis") or {})
    ids = work.get("source_ids") or {}
    links: list[str] = []
    if work.get("doi"):
        links.append(f'<a href="https://doi.org/{html_escape(work["doi"])}">DOI</a>')
    if ids.get("pmid"):
        links.append(f'<a href="https://pubmed.ncbi.nlm.nih.gov/{html_escape(ids["pmid"])}/">PubMed</a>')
    if ids.get("pmcid"):
        links.append(f'<a href="https://pmc.ncbi.nlm.nih.gov/articles/{html_escape(ids["pmcid"])}/">PMC</a>')
    if work.get("full_text_url"):
        links.append(f'<a href="{html_escape(work["full_text_url"])}">开放正文</a>')
    elif work.get("url"):
        links.append(f'<a href="{html_escape(work["url"])}">来源</a>')
    analysis_label = "综述五要素" if kind == "review" else "研究七要素"
    if wechat:
        return f"""
<section style="margin:0 0 28px;border:1px solid {COLORS['line']};border-radius:10px;overflow:hidden;background:#fff;">
  <p style="margin:0;padding:10px 14px;background:{COLORS['panel']};font-size:12px;color:#666;line-height:1.65;">{_paper_meta(work)}</p>
  <section style="padding:18px;">
    <p style="margin:0 0 5px;color:{COLORS['paper_green']};font-size:12px;font-weight:bold;">{_tier_badge(work, wechat=True)}学术文献 · {'综述' if kind == 'review' else '研究'}</p>
    <h3 style="margin:0;color:#1a365d;font-size:19px;line-height:1.45;">{html_escape(title_zh)}</h3>
    <p style="margin:6px 0 10px;color:{COLORS['muted']};font-size:13px;font-style:italic;line-height:1.5;">{html_escape(title_en)}</p>
    <p style="margin:8px 0;color:#586069;font-size:13px;"><strong>Authors:</strong> {html_escape(authors)}</p>
    <section style="margin:14px 0;padding:14px;border-radius:6px;background:{COLORS['paper_green_bg']};font-size:15px;line-height:1.75;"><strong style="display:block;margin-bottom:6px;color:{COLORS['paper_green_dark']};">摘要中文翻译</strong>{html_escape(abstract_zh)}</section>
    <section style="margin-top:12px;padding:12px 14px;border-left:4px solid {COLORS['amber_line']};background:{COLORS['amber_bg']};font-size:14px;line-height:1.65;"><strong style="color:{COLORS['amber']};">{analysis_label}</strong><dl>{_five_elements(elements_zh, _paper_fields(kind))}</dl></section>
    <p style="margin-top:13px;text-align:right;font-size:13px;font-weight:bold;">{' · '.join(links)}</p>
  </section>
</section>"""
    return f"""
<article class="card paper">
  <div class="meta-strip">{_paper_meta(work)}</div>
  <div class="card-body">
    <div style="font-size:12px;color:{COLORS['paper_green']};font-weight:700;margin-bottom:7px;"><span class="lang-zh">{_tier_badge(work)}学术文献 · {'综述' if kind == 'review' else '研究'}</span><span class="lang-en" hidden>{_tier_badge_en(work)}Academic literature · {'Review' if kind == 'review' else 'Research'}</span></div>
    <div class="lang-zh"><h3>{html_escape(title_zh)}</h3><div class="title-en">{html_escape(title_en)}</div><div class="authors"><strong>作者：</strong> {html_escape(authors)}</div><div class="translated-body"><strong>摘要中文翻译</strong>{html_escape(abstract_zh)}</div><details><summary>查看{analysis_label}</summary><dl class="five-grid">{_five_elements(elements_zh, _paper_fields(kind))}</dl></details></div>
    <div class="lang-en" hidden><h3>{html_escape(title_en)}</h3><div class="authors"><strong>Authors:</strong> {html_escape(authors)}</div><div class="original"><strong>Original Abstract</strong><br>{html_escape(original)}</div><details><summary>View {'review five-element analysis' if kind == 'review' else 'research seven-element analysis'}</summary><dl class="five-grid">{_five_elements(elements_en, _paper_fields_en(kind), 'Not reported in the supplied evidence.')}</dl></details></div>
    <div class="links"><span class="lang-zh">{' · '.join(links)}</span><span class="lang-en" hidden>{' · '.join(links)}</span></div>
  </div>
</article>"""


def supplementary_paper_card(work: dict[str, Any], *, wechat: bool = False) -> str:
    title_en = clean_space(work.get("title")) or "Untitled"
    title_zh = clean_space(work.get("title_zh")) or title_en
    authors = ", ".join((work.get("authors") or [])[:10]) or "Authors unavailable"
    ids = work.get("source_ids") or {}
    links: list[str] = []
    if work.get("doi"):
        links.append(f'<a href="https://doi.org/{html_escape(work["doi"])}">DOI</a>')
    if ids.get("pmid"):
        links.append(f'<a href="https://pubmed.ncbi.nlm.nih.gov/{html_escape(ids["pmid"])}/">PMID</a>')
    if ids.get("pmcid"):
        links.append(f'<a href="https://pmc.ncbi.nlm.nih.gov/articles/{html_escape(ids["pmcid"])}/">PMCID</a>')
    if work.get("url"):
        links.append(f'<a href="{html_escape(work["url"])}">来源</a>')
    notice_zh = clean_space(work.get("notice_zh")) or "摘要尚未公开。本条仅提供经过核验的出版元数据，不生成研究结论和结构化要素。"
    notice_en = clean_space(work.get("notice_en")) or "The abstract is not public. Only verified publication metadata are shown; no conclusions or structured elements are generated."
    if wechat:
        return f"""<section style="margin:0 0 14px;padding:12px;border:1px dashed #a0aec0;background:#f8fafc;border-radius:8px;">
<h3 style="margin:0;color:#2d3748;font-size:16px;line-height:1.5;">{html_escape(title_zh)}</h3>
<p style="margin:4px 0;color:#718096;font-size:12px;font-style:italic;">{html_escape(title_en)}</p>
<p style="margin:5px 0;font-size:12px;color:#586069;">{html_escape(work.get('journal'))} · {html_escape(work.get('canonical_publication_date') or work.get('availability_date'))}</p>
<p style="margin:5px 0;font-size:12px;color:#586069;">{html_escape(notice_zh)}</p>
<p style="margin:5px 0;text-align:right;font-size:12px;font-weight:700;">{' · '.join(links)}</p></section>"""
    return f"""<article class="card supplementary-card supplementary"><div class="meta-strip">{_paper_meta(work)}</div><div class="card-body">
<div class="lang-zh"><h3>{html_escape(title_zh)}</h3><div class="title-en">{html_escape(title_en)}</div><div class="authors"><strong>作者：</strong> {html_escape(authors)}</div><div class="supplementary-notice">{html_escape(notice_zh)}</div></div>
<div class="lang-en" hidden><h3>{html_escape(title_en)}</h3><div class="authors"><strong>Authors:</strong> {html_escape(authors)}</div><div class="supplementary-notice">{html_escape(notice_en)}</div></div>
<div class="links">{' · '.join(links)}</div></div></article>"""


def news_card(article: dict[str, Any], *, wechat: bool = False) -> str:
    title_en = clean_space(article.get("title")) or "Untitled"
    title_zh = clean_space(article.get("title_zh")) or title_en
    translated = clean_space(article.get("wechat_summary_zh") if wechat else article.get("content_zh") or article.get("summary_zh"))
    original = clean_space(article.get("content") or article.get("excerpt"))
    elements_zh = article.get("elements_zh") or article.get("analysis_zh") or {}
    elements_en = article.get("elements_en") or article.get("analysis_en") or ((article.get("analysis") or {}).get("analysis") or {})
    link = html_escape(article.get("resolved_url") or article.get("url"))
    fallback_note = ""
    if article.get("translation_status") == "english_fallback":
        fallback_note = '<p class="translation-fallback-note" style="font-size:12px;color:#975a16;">中文翻译暂不可用；以下中文显示位置使用已核验英文内容填充。</p>'
    meta = " &nbsp;|&nbsp; ".join(x for x in [_date_label("Source", article.get("publisher") or article.get("source")), _date_label("Published", article.get("published_date"))] if x)
    if wechat:
        return f"""
<section style="margin:0 0 20px;border:1px solid #fed7d7;border-radius:9px;overflow:hidden;background:#fff;">
  <p style="margin:0;padding:10px 14px;background:{COLORS['news_red_bg']};font-size:12px;color:#666;line-height:1.65;">{meta}</p>
  <section style="padding:17px;"><p style="margin:0 0 6px;">{_tier_badge(article, wechat=True)}</p><h3 style="margin:0;color:#9b2c2c;font-size:18px;line-height:1.45;">{html_escape(title_zh)}</h3>
    <p style="margin:5px 0;color:{COLORS['muted']};font-size:13px;line-height:1.5;">{html_escape(title_en)}</p>{fallback_note}
    <section style="margin:13px 0;padding:14px;border-radius:6px;background:{COLORS['news_red_bg']};font-size:15px;line-height:1.75;"><strong style="display:block;margin-bottom:6px;color:{COLORS['news_red']};">公众号新闻精炼</strong>{html_escape(translated)}</section>
    <section style="padding:12px 14px;border-left:4px solid {COLORS['amber_line']};background:{COLORS['amber_bg']};font-size:14px;line-height:1.65;"><strong style="color:{COLORS['amber']};">新闻五要素</strong><dl>{_five_elements(elements_zh, _news_fields())}</dl></section>
    <p style="text-align:right;font-size:13px;font-weight:bold;"><a href="{link}">查看原始报道</a></p>
  </section>
</section>"""
    return f"""<article class="card news"><div class="meta-strip">{meta}</div><div class="card-body">
<div class="lang-zh">{_tier_badge(article)}<h3 style="color:#9b2c2c">{html_escape(title_zh)}</h3><div class="title-en">{html_escape(title_en)}</div>{fallback_note}<div class="translated-body"><strong>完整新闻中文摘要</strong>{html_escape(translated)}</div><details><summary>查看新闻五要素</summary><dl class="five-grid">{_five_elements(elements_zh, _news_fields())}</dl></details></div>
<div class="lang-en" hidden>{_tier_badge_en(article)}<h3 style="color:#9b2c2c">{html_escape(title_en)}</h3><div class="original"><strong>{html_escape('Syndicated Summary' if article.get('content_status') == 'syndicated_summary' else 'Fetched Original Body')}</strong><br>{html_escape(original)}</div><details><summary>View news five-element analysis</summary><dl class="five-grid">{_five_elements(elements_en, _news_fields_en(), 'Not reported in the supplied evidence.')}</dl></details></div>
<div class="links"><span class="lang-zh"><a href="{link}">查看原始报道</a></span><span class="lang-en" hidden><a href="{link}">View original report</a></span></div></div></article>"""


def _overview_html(block: dict[str, Any], title: str, *, wechat: bool = False) -> str:
    findings_zh = "".join(f"<li>{html_escape(item)}</li>" for item in (block.get("key_findings_zh") or []))
    findings_en = "".join(f"<li>{html_escape(item)}</li>" for item in (block.get("key_findings_en") or []))
    if wechat:
        return f"""<section style="padding:18px;background:{COLORS['amber_bg']};border-bottom:4px solid {COLORS['amber_line']};"><h2 style="margin:0 0 7px;color:{COLORS['amber']};font-size:18px;">{html_escape(title)}</h2><h3 style="margin:0 0 8px;font-size:17px;color:#744210;">{html_escape(block.get('headline_zh'))}</h3><p style="margin:0 0 8px;font-size:14px;line-height:1.75;">{html_escape(block.get('lead_zh'))}</p><ul style="margin:7px 0;padding-left:20px;font-size:14px;line-height:1.7;">{findings_zh}</ul><p style="margin:7px 0;font-size:14px;line-height:1.7;">{html_escape(block.get('trend_or_risk_zh'))}</p><p style="margin:7px 0 0;color:#718096;font-size:12px;line-height:1.65;">证据提醒：{html_escape(block.get('caveats_zh'))}</p></section>"""
    return f"""<section class="overview"><h2>{html_escape(title)}</h2><div class="lang-zh"><h3>{html_escape(block.get('headline_zh'))}</h3><p>{html_escape(block.get('lead_zh'))}</p><ul>{findings_zh}</ul><p>{html_escape(block.get('trend_or_risk_zh'))}</p><p style="font-size:12px;color:#718096;">证据提醒：{html_escape(block.get('caveats_zh'))}</p></div><div class="lang-en" hidden><h3>{html_escape(block.get('headline_en'))}</h3><p>{html_escape(block.get('lead_en') or block.get('brief_en'))}</p><ul>{findings_en}</ul><p>{html_escape(block.get('trend_or_risk_en'))}</p><p style="font-size:12px;color:#718096;">Evidence caveat: {html_escape(block.get('caveats_en'))}</p></div></section>"""


def _overview_statlines(issue: dict[str, Any], *, wechat: bool = False) -> str:
    funnel = issue.get("retrieval_funnel") or {}
    papers = funnel.get("papers") or {}
    news = funnel.get("news") or {}
    zh_paper = (
        f"文献概览：数据库记录 {int(papers.get('raw') or 0):,} 条；规范发表日期窗口内 {int(papers.get('after_window') or 0):,} 条；"
        f"跨库去重后 {int(papers.get('after_dedup') or 0):,} 条；终审后形成 {int(papers.get('relevant_catalog_after_completion_and_identity_gate') or 0):,} 条可核验目录；"
        f"其中有摘要或全文 {int(papers.get('evidence_ready_catalog') or 0):,} 条、仅元数据 {int(papers.get('metadata_only_catalog') or 0):,} 条；"
        f"主报告 {int(papers.get('primary_displayed') or 0):,} 篇，补充文献 {int(papers.get('supplementary_displayed') or 0):,} 篇。"
    )
    zh_news = f"新闻概览：检索 {int(news.get('raw') or 0):,} 条；时间窗内 {int(news.get('after_window') or 0):,} 条；最终展示 {int(news.get('displayed') or 0):,} 条。新闻资格不受公众号字符上限影响。"
    en_paper = (
        f"Literature: {int(papers.get('raw') or 0):,} database records; {int(papers.get('after_window') or 0):,} within the canonical publication window; "
        f"{int(papers.get('after_dedup') or 0):,} after cross-source deduplication; {int(papers.get('relevant_catalog_after_completion_and_identity_gate') or 0):,} verified relevant catalog records; "
        f"{int(papers.get('evidence_ready_catalog') or 0):,} with verified abstract/full text and {int(papers.get('metadata_only_catalog') or 0):,} metadata-only; "
        f"{int(papers.get('primary_displayed') or 0):,} primary reports and {int(papers.get('supplementary_displayed') or 0):,} supplementary records."
    )
    en_news = f"News: {int(news.get('raw') or 0):,} retrieved; {int(news.get('after_window') or 0):,} in the reporting window; {int(news.get('displayed') or 0):,} displayed. Channel length never determines eligibility."
    note_zh = "Top50表示进入深度主报告，而不是删除阈值；其余通过终审的文献进入补充文献区。完整审计保存在 data/audit。"
    note_en = "Top 50 means selection for deep reporting, not deletion. Other verified relevant records remain supplementary. Full audits are stored under data/audit."
    if wechat:
        return f'<section style="padding:12px 16px;background:#edf2f7;border-bottom:1px solid #e2e8f0;font-size:13px;line-height:1.7;font-weight:700;color:#2d3748;"><p style="margin:2px 0;">{html_escape(zh_paper)}</p><p style="margin:2px 0;">{html_escape(zh_news)}</p><p style="margin:7px 0 0;font-size:11px;font-weight:400;color:#586069;">{html_escape(note_zh)}</p></section>'
    return f'<div class="overview-statline"><div class="lang-zh"><p>{html_escape(zh_paper)}</p><p>{html_escape(zh_news)}</p></div><div class="lang-en" hidden><p>{html_escape(en_paper)}</p><p>{html_escape(en_news)}</p></div></div><div class="statistics-note"><div class="lang-zh">{html_escape(note_zh)}</div><div class="lang-en" hidden>{html_escape(note_en)}</div></div>'


def _source_health(issue: dict[str, Any]) -> str:
    """Compatibility audit renderer. v15 stores this information in JSON and does not call it on the public page."""
    rows = ((issue.get("source_status") or {}).get("sources") or [])
    if not rows:
        return ""
    return "<details class=\"source-health\"><summary>Backend source audit</summary><p>" + html_escape(
        "; ".join(f"{x.get('source')}: {x.get('health')}" for x in rows)
    ) + "</p></details>"


def _section(title: str, cls: str, cards: list[str]) -> str:
    if not cards:
        return ""
    return f'<section><h2 class="section-title {html_escape(cls)}">{html_escape(title)}</h2>{"".join(cards)}</section>'


def render_site(issue: dict[str, Any], output_dir: Path) -> None:
    site_dir = output_dir / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    papers = issue.get("papers") or []
    supplementary = issue.get("supplementary_papers") or []
    research = [p for p in papers if p.get("paper_type") == "research"]
    reviews = [p for p in papers if p.get("paper_type") == "review"]
    news = issue.get("news") or []
    overview = issue.get("overview") or {}
    sections = [
        _section("📘 主报告：研究论文 / Primary Research", "research", [paper_card(x) for x in research]),
        _section("📗 主报告：综述 / Primary Reviews", "review", [paper_card(x) for x in reviews]),
        _section("📎 补充文献 / Supplementary Literature", "supplementary", [supplementary_paper_card(x) for x in supplementary]),
        _section("🚨 突发动态与新闻 / Health News", "news", [news_card(x) for x in news]),
    ]
    overview_html = _overview_html(overview.get("literature") or {}, "📚 本期文献进展 / Literature Brief") + _overview_html(overview.get("news") or {}, "📰 本期新闻动态 / News Brief")
    html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html_escape(issue['title_zh'])}</title><style>{SITE_CSS}</style></head><body><main class="page">
<header class="hero"><img src="assets/cover.jpg" alt="{html_escape(issue['title_zh'])}"><div class="hero-text"><div class="lang-zh"><h1>{html_escape(issue['title_zh'])}</h1><p>{html_escape(issue['issue_date'])} | 文献与公共卫生新闻 | {html_escape(issue['window_start'])}—{html_escape(issue['window_end'])}</p></div><div class="lang-en" hidden><h1>{html_escape(issue['title_en'])}</h1><p>{html_escape(issue['issue_date'])} | Literature and public-health news | {html_escape(issue['window_start'])}—{html_escape(issue['window_end'])}</p></div></div></header>
{_overview_statlines(issue)}{overview_html}
<div class="toolbar"><button class="language-toggle" data-language="zh">中文</button><button class="language-toggle" data-language="en">English</button></div>
<div class="stats"><div><strong>{len(research)}</strong><span class="lang-zh">主报告研究</span><span class="lang-en" hidden>Primary research</span></div><div><strong>{len(reviews)}</strong><span class="lang-zh">主报告综述</span><span class="lang-en" hidden>Primary reviews</span></div><div><strong>{len(supplementary)}</strong><span class="lang-zh">补充文献</span><span class="lang-en" hidden>Supplementary</span></div><div><strong>{len(news)}</strong><span class="lang-zh">有效新闻</span><span class="lang-en" hidden>Validated news</span></div><div><strong>{issue.get('metrics',{}).get('translated',0)}</strong><span class="lang-zh">深度双语记录</span><span class="lang-en" hidden>Deep bilingual records</span></div></div>
<div class="content">{"".join(sections)}</div>
<footer><span class="lang-zh">顶部文献总结仅基于具有可核验摘要或全文的主报告；补充文献仅展示经过核验的出版元数据。</span><span class="lang-en" hidden>The literature brief is based only on primary reports with verified abstracts or full text. Supplementary records show verified publication metadata only.</span></footer>
</main><script>{SITE_JS}</script></body></html>'''
    (site_dir / "index.html").write_text(html, encoding="utf-8")
    items: list[str] = []
    for item in papers[:10] + news[:10]:
        title = item.get("title_zh") or item.get("title")
        link = f"https://doi.org/{item.get('doi')}" if item.get("doi") else item.get("resolved_url") or item.get("url") or ""
        description = item.get("abstract_zh") or item.get("content_zh") or item.get("summary_zh") or ""
        items.append(f"<item><title>{html_escape(title)}</title><link>{html_escape(link)}</link><description>{html_escape(description)}</description></item>")
    feed = f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{html_escape(issue["title_zh"])}</title><link>./</link><description>{html_escape(issue["title_en"])}</description>{"".join(items)}</channel></rss>'
    (site_dir / "feed.xml").write_text(feed, encoding="utf-8")


def render_wechat_package(issue: dict[str, Any], output_dir: Path, cover_meta: dict[str, Any]) -> None:
    package = output_dir / "wechat-package"
    package.mkdir(parents=True, exist_ok=True)
    papers = issue.get("papers") or []
    supplementary = issue.get("supplementary_papers") or []
    news = issue.get("news") or []
    overview = issue.get("overview") or {}
    source_url = os.getenv("PIF_CONTENT_SOURCE_URL", "").strip()
    supplementary_preview = supplementary[:20]
    supplementary_html = "".join(supplementary_paper_card(x, wechat=True) for x in supplementary_preview)
    if len(supplementary) > len(supplementary_preview):
        supplementary_html += f'<p style="font-size:12px;color:#586069;">另有 {len(supplementary)-len(supplementary_preview)} 篇补充文献，请在完整网页中查看。</p>'
    if source_url:
        supplementary_html += f'<p style="text-align:right;font-weight:700;"><a href="{html_escape(source_url)}">查看完整网页</a></p>'
    body = f'''<section style="font-family:Arial,'Noto Sans CJK SC',sans-serif;color:#333;line-height:1.75;">
<section style="padding:22px;background:{COLORS['navy']};color:#fff;text-align:center;"><h1 style="margin:0;font-size:24px;">{html_escape(issue['title_zh'])}</h1><p style="margin:8px 0 0;font-size:13px;opacity:.85;">{html_escape(issue['issue_date'])} | 文献与公共卫生新闻</p></section>
{_overview_statlines(issue, wechat=True)}
{_overview_html(overview.get('literature') or {}, '📚 本期文献进展', wechat=True)}
{_overview_html(overview.get('news') or {}, '📰 本期新闻动态', wechat=True)}
<h2 style="border-left:6px solid {COLORS['paper_green']};padding-left:12px;color:{COLORS['paper_green']};font-size:20px;">📘 主报告文献</h2>
{"".join(paper_card(x, wechat=True) for x in papers) or '<p>本期无满足主报告证据标准的文献。</p>'}
<h2 style="border-left:6px solid #a0aec0;padding-left:12px;color:#4a5568;font-size:20px;">📎 补充文献目录</h2>
{supplementary_html or '<p>本期无补充文献。</p>'}
<h2 style="border-left:6px solid {COLORS['news_red']};padding-left:12px;color:{COLORS['news_red']};font-size:20px;">🚨 突发动态与新闻</h2>
{"".join(news_card(x, wechat=True) for x in news) or '<p>本期无通过新闻资格门禁的记录。</p>'}
<p style="padding:16px;background:{COLORS['navy']};color:#fff;text-align:center;font-size:11px;">新闻资格与公众号长度限制相互独立；标准数据保留完整内容，公众号仅在渲染阶段生成精简版。</p></section>'''
    (package / "article.html").write_text(body, encoding="utf-8")
    literature_overview = overview.get("literature") or {}
    news_overview = overview.get("news") or {}
    digest_source = clean_space(literature_overview.get("lead_zh") or literature_overview.get("headline_zh") or news_overview.get("lead_zh") or issue.get("title_zh"))
    manifest = {
        "schema_version": 2,
        "contract": "pathogen-wechat-package/v2",
        "publish_key": issue["issue_id"],
        "profile_id": issue["profile_id"],
        "report_date": issue["issue_date"],
        "title": f"{issue['title_zh']}｜{issue['issue_date']}",
        "digest": digest_source[:120],
        "content_file": "article.html",
        "content_source_url": source_url,
        "show_cover_pic": 1,
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
        "images": [],
        "cover": {
            "file": "cover.jpg",
            "sha256": cover_meta.get("cover_sha256"),
            "asset_key": issue["profile_id"],
            "generator": cover_meta.get("generator"),
            "profile_fingerprint": cover_meta.get("profile_fingerprint"),
        },
        "source": {
            "profile_id": issue["profile_id"],
            "issue_id": issue["issue_id"],
            "generated_at": issue["generated_at"],
            "issue_schema_version": issue.get("schema_version"),
            "primary_papers": len(papers),
            "supplementary_papers": len(supplementary),
            "news": len(news),
        },
    }
    dump_json(package / "manifest.json", manifest)
