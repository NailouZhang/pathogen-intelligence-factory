from __future__ import annotations

import copy
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .utils import clean_space, dump_json, html_escape, truncate
from .public_display import build_display_issue, sanitize_public_text
from .language_contract import detect_text_language, is_verified_english, language_label

COLORS = {
    "navy": "#2c3e50", "paper_green": "#27ae60", "paper_green_dark": "#1e7e34",
    "paper_green_bg": "#f0fff4", "news_red": "#c53030", "news_red_bg": "#fff5f5",
    "amber": "#c05621", "amber_bg": "#fffcf0", "amber_line": "#fbd38d",
    "line": "#e2e8f0", "panel": "#f8fafc", "muted": "#718096",
}

SITE_CSS = """
:root{--navy:#2c3e50;--green:#27ae60;--red:#c53030;--amber:#c05621;--line:#e2e8f0;--muted:#718096}
*{box-sizing:border-box}body{margin:0;background:#f4f7f9;color:#333;font-family:Arial,'Noto Sans CJK SC',sans-serif}a{color:#0366d6;text-decoration:none}.page{max-width:1040px;margin:18px auto;background:#fff;border-radius:15px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.1)}.hero{background:var(--navy);color:#fff;text-align:center}.hero img{width:100%;display:block;max-height:442px;object-fit:cover}.hero-text{padding:16px 26px 20px}.hero h1{margin:0;font-size:30px}.hero p{margin:6px 0 0;opacity:.82}.overview{padding:14px 22px;background:#fffcf0;border-bottom:3px solid #fbd38d}.overview h2{color:var(--amber);margin:0 0 8px;font-size:19px}.overview ul{margin:5px 0 0;padding-left:21px;line-height:1.7}.overview li{margin:4px 0}.overview-statline{padding:7px 22px;background:#fff;border-bottom:1px solid var(--line);font-size:12px;line-height:1.5;font-weight:400;color:#888}.overview-statline p{margin:1px 0}.toolbar{display:flex;justify-content:flex-end;gap:8px;padding:9px 24px;border-bottom:1px solid var(--line)}button{font:inherit;border:1px solid var(--line);background:#fff;padding:6px 10px;cursor:pointer}.stats{display:grid;grid-template-columns:repeat(6,1fr);border-bottom:1px solid var(--line)}.stats div{padding:12px 8px;text-align:center;border-right:1px solid var(--line)}.stats div:last-child{border-right:0}.stats strong{font-size:25px;color:var(--red);display:block}.content{padding:22px}.content section{margin-top:24px}.content section:first-child{margin-top:0}.section-title{font-size:21px;padding-left:12px;margin:0 0 11px;border-left:6px solid}.section-title.research,.section-title.review{color:var(--green);border-color:var(--green)}.section-title.supplementary{color:#4a5568;border-color:#a0aec0}.section-title.news{color:var(--red);border-color:var(--red)}.card{margin-bottom:10px;border:1px solid var(--line);border-radius:9px;overflow:hidden;background:#fff}.supplementary-card{border-style:dashed;background:#fbfdff}.meta-strip{padding:7px 12px;background:#f8fafc;border-bottom:1px solid var(--line);font-size:12px;color:#666;line-height:1.55}.card-body{padding:12px 14px}.card h3{font-size:18px;color:#1a365d;line-height:1.45;margin:0}.title-en{font-size:13px;color:var(--muted);font-style:italic;margin-top:3px;line-height:1.45}.authors{font-size:13px;color:#586069;margin:5px 0}.translated-body{font-size:15px;line-height:1.75;margin:7px 0;padding:10px 12px;border-radius:6px;background:#f0fff4}.news .translated-body{background:#fff5f5}.translated-body strong{display:block;margin-bottom:4px;color:#1e7e34}.news .translated-body strong{color:var(--red)}details{margin-top:7px;border-top:1px dotted var(--line);border-bottom:1px dotted var(--line);padding:6px 0}summary{cursor:pointer;font-weight:700;color:var(--amber)}.five-grid{display:grid;grid-template-columns:88px 1fr;gap:5px 9px;margin-top:6px;font-size:14px;line-height:1.55}.five-grid dt{font-weight:700;color:var(--amber)}.five-grid dd{margin:0}.original{font-size:13px;line-height:1.65;color:#666;background:#f8fafc;padding:9px 11px;margin-top:6px;border-radius:6px}.links{text-align:right;font-size:13px;margin-top:7px;font-weight:700}.tier-badge{display:inline-block;padding:2px 7px;border-radius:999px;font-size:11px;font-weight:700;margin-right:6px}.tier-A{background:#e6fffa;color:#06735f}.tier-B{background:#ebf8ff;color:#2b6cb0}.tier-C{background:#f7fafc;color:#718096;border:1px solid #e2e8f0}footer{background:var(--navy);color:#fff;padding:16px;text-align:center;font-size:11px;line-height:1.6}[hidden]{display:none!important}@media(max-width:700px){.page{margin:0;border-radius:0}.content{padding:14px}.stats{grid-template-columns:repeat(2,1fr)}.five-grid{grid-template-columns:70px 1fr}.toolbar{justify-content:center;padding:8px}}
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


def _paper_meta(work: dict[str, Any], *, wechat: bool = False) -> str:
    items = [
        _date_label("Journal", work.get("journal") or "Unknown Journal"),
        _date_label("Canonical publication date", work.get("canonical_publication_date") or work.get("availability_date")),
    ]
    if not wechat:
        status_labels = {"in_window": "Current window", "in_window_month_precision": "Current window (month precision)", "future_scheduled": "Future scheduled publication"}
        items.extend([
            _date_label("Date basis", work.get("canonical_publication_date_basis") or work.get("availability_date_basis")),
            _date_label("Publication status", status_labels.get(work.get("publication_date_status"), work.get("publication_date_status"))),
            _date_label("DOI", work.get("doi")),
        ])
        citation = ", ".join(str(x) for x in [work.get("year"), work.get("volume"), work.get("issue"), work.get("pages")] if x not in (None, ""))
        if citation:
            items.append(_date_label("Citation", citation))
    return " &nbsp;|&nbsp; ".join(x for x in items if x)


def _paper_fields(kind: str) -> list[tuple[str, str]]:
    if kind == "review":
        return [("范围与问题", "scope_and_question"), ("证据基础与方法", "evidence_base_and_review_method"), ("共识与结论", "consensus_and_key_conclusions"), ("争议与缺口", "controversies_and_evidence_gaps"), ("科研与实践启示", "research_and_practice_implications")]
    return [("问题与背景", "research_question_and_background"), ("设计与对象", "study_design_and_population"), ("核心方法", "methods"), ("主要结果", "main_results"), ("解释与创新", "interpretation_and_novelty"), ("科研与公卫意义", "scientific_and_public_health_significance"), ("局限与证据强度", "limitations_and_evidence_strength")]


def _paper_fields_en(kind: str) -> list[tuple[str, str]]:
    if kind == "review":
        return [("Scope and question", "scope_and_question"), ("Evidence base and method", "evidence_base_and_review_method"), ("Consensus and conclusions", "consensus_and_key_conclusions"), ("Controversies and gaps", "controversies_and_evidence_gaps"), ("Research and practice implications", "research_and_practice_implications")]
    return [("Question and background", "research_question_and_background"), ("Design and population", "study_design_and_population"), ("Core methods", "methods"), ("Main results", "main_results"), ("Interpretation and novelty", "interpretation_and_novelty"), ("Scientific and public-health significance", "scientific_and_public_health_significance"), ("Limitations and evidence strength", "limitations_and_evidence_strength")]


def _news_fields() -> list[tuple[str, str]]:
    return [("时间", "time"), ("地点与对象", "location_and_population"), ("事件", "event"), ("规模、影响与风险", "scale_impact_and_risk"), ("应对、状态与不确定性", "response_status_and_uncertainty")]


def _news_fields_en() -> list[tuple[str, str]]:
    return [("Time", "time"), ("Location and population", "location_and_population"), ("Event", "event"), ("Scale, impact and risk", "scale_impact_and_risk"), ("Response, status and uncertainty", "response_status_and_uncertainty")]


def _five_elements(
    data: dict[str, Any],
    fields: list[tuple[str, str]],
    missing_text: str = "原始证据未报告",
    *,
    language: str = "zh",
) -> str:
    rows: list[str] = []
    for label, key in fields:
        value = clean_space(data.get(key)) or missing_text
        if language == "en" and not is_verified_english(value):
            value = "The supplied source-language evidence could not be converted into verified English for this element."
        rows.append(f'<dt>{html_escape(label)}</dt><dd lang="{html_escape(language)}">{html_escape(value)}</dd>')
    return "".join(rows)


def _source_language(record: dict[str, Any], text: str) -> str:
    return detect_text_language(text, record.get("source_language") or record.get("language"))


def _original_source_block(
    record: dict[str, Any],
    text: str,
    label: str,
    *,
    metadata_role: str = "",
) -> str:
    value = clean_space(text)
    if not value:
        return ""
    language = _source_language(record, value)
    extra_class = " original-title-metadata" if metadata_role == "title" else ""
    role_attr = f' data-metadata-role="{html_escape(metadata_role)}"' if metadata_role else ""
    return (
        f'<div class="original source-original{extra_class}" lang="{html_escape(language)}" '
        f'data-source-language="{html_escape(language)}"{role_attr}><strong>{html_escape(label)} '
        f'({html_escape(language_label(language))})</strong><br>{html_escape(value)}</div>'
    )


def _english_display_title(record: dict[str, Any], original_title: str, kind_label: str) -> str:
    translated = clean_space(record.get("title_en"))
    if translated and is_verified_english(translated):
        return translated
    original = clean_space(original_title)
    if original and is_verified_english(original):
        return original
    language = _source_language(record, original)
    return f"{kind_label} ({language_label(language)} original; verified English title unavailable)"


def _tier_badge(item: dict[str, Any], *, wechat: bool = False) -> str:
    tier = str(item.get("priority_tier") or "C").upper()
    label = {"A": "A 高优先级", "B": "B 常规重要", "C": "C 补充信息"}.get(tier, "C 补充信息")
    if wechat:
        bg = {"A": "#e6fffa", "B": "#ebf8ff", "C": "#f7fafc"}.get(tier, "#f7fafc")
        fg = {"A": "#06735f", "B": "#2b6cb0", "C": "#718096"}.get(tier, "#718096")
        return f'<span style="display:inline-block;padding:2px 7px;border-radius:999px;background:{bg};color:{fg};font-size:11px;font-weight:700;margin-right:6px;">{label}</span>'
    return f'<span class="tier-badge tier-{html_escape(tier)}">{label}</span>'


def _tier_badge_en(item: dict[str, Any]) -> str:
    tier = str(item.get("priority_tier") or "C").upper()
    label = {"A": "A high priority", "B": "B routine priority", "C": "C supplementary"}.get(tier, "C supplementary")
    return f'<span class="tier-badge tier-{html_escape(tier)}">{label}</span>'


def _analysis_quality_banner(issue: dict[str, Any], *, wechat: bool = False) -> str:
    return ""


def paper_card(work: dict[str, Any], *, wechat: bool = False) -> str:
    if wechat and work.get("wechat_omitted"):
        return ""
    kind = work.get("paper_type") or "research"
    raw_title = clean_space(work.get("title_original") or work.get("title"))
    title_en = clean_space(work.get("wechat_title_en")) if wechat else ""
    title_en = title_en or _english_display_title(work, raw_title, "Academic paper")
    title_zh = clean_space(work.get("wechat_title_zh") if wechat else work.get("title_zh")) or clean_space(work.get("title_zh")) or raw_title or title_en
    original_title_block = _original_source_block(work, raw_title, "Original title") if raw_title and not is_verified_english(raw_title) else ""
    abstract_zh = clean_space(work.get("wechat_abstract_zh") if wechat else (work.get("abstract_zh") or work.get("summary_zh")))
    original = clean_space(work.get("abstract_original") or work.get("abstract") or work.get("full_text_excerpt"))
    original_block = _original_source_block(work, original, "Original abstract")
    authors = clean_space(work.get("wechat_authors")) if wechat else ""
    authors = authors or ", ".join((work.get("authors") or [])[:10]) or "Authors unavailable"
    elements_zh = (work.get("wechat_elements_zh") if wechat else None) or work.get("elements_zh") or work.get("analysis_zh") or {}
    elements_en = work.get("elements_en") or work.get("analysis_en") or ((work.get("analysis") or {}).get("analysis") or {})
    hide_details = bool(work.get("wechat_compact_details_removed"))
    ids = work.get("source_ids") or {}
    links: list[str] = []
    if not wechat:
        if work.get("doi"): links.append(f'<a href="https://doi.org/{html_escape(work["doi"])}">DOI</a>')
        if ids.get("pmid"): links.append(f'<a href="https://pubmed.ncbi.nlm.nih.gov/{html_escape(ids["pmid"])}/">PubMed</a>')
        if ids.get("pmcid"): links.append(f'<a href="https://pmc.ncbi.nlm.nih.gov/articles/{html_escape(ids["pmcid"])}/">PMC</a>')
        if work.get("full_text_url"): links.append(f'<a href="{html_escape(work["full_text_url"])}">开放正文</a>')
        elif work.get("url"): links.append(f'<a href="{html_escape(work["url"])}">来源</a>')
    analysis_label = "综述五要素" if kind == "review" else "研究七要素"
    if wechat:
        deep_html = "" if hide_details else f'''<section style="margin:7px 0;padding:10px 12px;border-radius:6px;background:{COLORS['paper_green_bg']};font-size:15px;line-height:1.75;"><strong style="display:block;margin-bottom:4px;color:{COLORS['paper_green_dark']};">摘要中文翻译</strong>{html_escape(abstract_zh)}</section><section style="margin-top:6px;padding:9px 11px;border-left:4px solid {COLORS['amber_line']};background:{COLORS['amber_bg']};font-size:14px;line-height:1.65;"><strong style="color:{COLORS['amber']};">{analysis_label}</strong><dl style="margin:5px 0 0;">{_five_elements(elements_zh, _paper_fields(kind))}</dl></section>'''
        return f'''<section style="margin:0 0 10px;border:1px solid {COLORS['line']};border-radius:9px;overflow:hidden;background:#fff;"><p style="margin:0;padding:7px 11px;background:{COLORS['panel']};font-size:12px;color:#666;line-height:1.55;">{_paper_meta(work, wechat=True)}</p><section style="padding:11px 13px;"><p style="margin:0 0 3px;color:{COLORS['paper_green']};font-size:12px;font-weight:bold;">{_tier_badge(work, wechat=True)}学术文献 · {'综述' if kind == 'review' else '研究'}</p><h3 style="margin:0;color:#1a365d;font-size:18px;line-height:1.45;">{html_escape(title_zh)}</h3><p style="margin:3px 0 5px;color:{COLORS['muted']};font-size:13px;font-style:italic;line-height:1.45;">{html_escape(title_en)}</p><p style="margin:4px 0;color:#586069;font-size:13px;"><strong>Authors:</strong> {html_escape(authors)}</p>{deep_html}</section></section>'''
    return f'''<article class="card paper"><div class="meta-strip">{_paper_meta(work)}</div><div class="card-body"><div style="font-size:12px;color:{COLORS['paper_green']};font-weight:700;margin-bottom:4px;"><span class="lang-zh">{_tier_badge(work)}学术文献 · {'综述' if kind == 'review' else '研究'}</span><span class="lang-en" hidden>{_tier_badge_en(work)}Academic literature · {'Review' if kind == 'review' else 'Research'}</span></div><div class="lang-zh"><h3>{html_escape(title_zh)}</h3><div class="title-en">{html_escape(title_en)}</div><div class="authors"><strong>作者：</strong> {html_escape(authors)}</div><div class="translated-body"><strong>摘要中文翻译</strong>{html_escape(abstract_zh)}</div><details><summary>查看{analysis_label}</summary><dl class="five-grid">{_five_elements(elements_zh, _paper_fields(kind))}</dl></details></div><div class="lang-en" hidden><h3>{html_escape(title_en)}</h3>{original_title_block}<div class="authors"><strong>Authors:</strong> {html_escape(authors)}</div>{original_block}<details><summary>View {'review five-element analysis' if kind == 'review' else 'research seven-element analysis'}</summary><dl class="five-grid">{_five_elements(elements_en, _paper_fields_en(kind), 'Not reported in the supplied evidence.', language='en')}</dl></details></div><div class="links"><span class="lang-zh">{' · '.join(links)}</span><span class="lang-en" hidden>{' · '.join(links)}</span></div></div></article>'''


def _supplementary_scope_notice(item: dict[str, Any], *, wechat: bool = False) -> str:
    related = (
        item.get("display_mode") == "supplementary_related"
        or item.get("relevance_route") == "supplementary_related"
        or item.get("supplementary_reason") == "biologically_related_non_target_entity"
    )
    if not related:
        return ""
    zh = sanitize_public_text(item.get("notice_zh")) or "与目标病原相关的比较或背景资料。"
    en = sanitize_public_text(item.get("notice_en")) or "Comparative or background material related to the target pathogen."
    if wechat:
        return f'<p data-metadata-role="related-material" style="margin:4px 0 6px;padding:5px 8px;border-radius:5px;background:#edf2f7;color:#4a5568;font-size:12px;line-height:1.55;"><strong>相关资料：</strong>{html_escape(zh or en)}</p>'
    return f'<div data-metadata-role="related-material" class="supplementary-scope-note"><span class="lang-zh"><strong>相关资料：</strong>{html_escape(zh or en)}</span><span class="lang-en" hidden><strong>Related material:</strong> {html_escape(en or zh)}</span></div>'


def supplementary_paper_card(work: dict[str, Any], *, wechat: bool = False) -> str:
    if wechat and work.get("wechat_omitted"):
        return ""
    raw_title = clean_space(work.get("title_original") or work.get("title"))
    title_en = _english_display_title(work, raw_title, "Academic paper")
    title_zh = clean_space(work.get("title_zh")) or raw_title or title_en
    original_title_block = _original_source_block(work, raw_title, "Original title", metadata_role="title") if raw_title and not is_verified_english(raw_title) else ""
    authors = ", ".join((work.get("authors") or [])[:10]) or "Authors unavailable"
    ids = work.get("source_ids") or {}
    links: list[str] = []
    if not wechat:
        if work.get("doi"): links.append(f'<a href="https://doi.org/{html_escape(work["doi"])}">DOI</a>')
        if ids.get("pmid"): links.append(f'<a href="https://pubmed.ncbi.nlm.nih.gov/{html_escape(ids["pmid"])}/">PMID</a>')
        if ids.get("pmcid"): links.append(f'<a href="https://pmc.ncbi.nlm.nih.gov/articles/{html_escape(ids["pmcid"])}/">PMCID</a>')
        if work.get("url"): links.append(f'<a href="{html_escape(work["url"])}">来源</a>')
    scope_notice = _supplementary_scope_notice(work, wechat=wechat)
    if wechat:
        return f'''<section style="margin:0 0 7px;padding:9px 11px;border:1px dashed #a0aec0;background:#f8fafc;border-radius:8px;">{scope_notice}<h3 style="margin:0;color:#2d3748;font-size:16px;line-height:1.45;">{html_escape(title_zh)}</h3><p style="margin:2px 0;color:#718096;font-size:12px;font-style:italic;">{html_escape(title_en)}</p><p style="margin:3px 0;font-size:12px;color:#586069;">{html_escape(work.get('journal'))} · {html_escape(work.get('canonical_publication_date') or work.get('availability_date'))}</p></section>'''
    return f'''<article class="card supplementary-card supplementary"><div class="meta-strip">{_paper_meta(work)}</div><div class="card-body">{scope_notice}<div class="lang-zh"><h3>{html_escape(title_zh)}</h3><div class="title-en">{html_escape(title_en)}</div><div class="authors"><strong>作者：</strong> {html_escape(authors)}</div></div><div class="lang-en" hidden><h3>{html_escape(title_en)}</h3>{original_title_block}<div class="authors"><strong>Authors:</strong> {html_escape(authors)}</div></div><div class="links">{' · '.join(links)}</div></div></article>'''


def news_card(article: dict[str, Any], *, wechat: bool = False) -> str:
    if wechat and article.get("wechat_omitted"):
        return ""
    raw_title = clean_space(article.get("title_original") or article.get("title"))
    title_en = clean_space(article.get("wechat_title_en")) if wechat else ""
    title_en = title_en or _english_display_title(article, raw_title, "News article")
    title_zh = clean_space(article.get("wechat_title_zh") if wechat else article.get("title_zh")) or clean_space(article.get("title_zh")) or raw_title or title_en
    original_title_block = _original_source_block(article, raw_title, "Original title") if raw_title and not is_verified_english(raw_title) else ""
    brief_zh_full = clean_space(article.get("content_zh") or article.get("summary_zh") or article.get("wechat_summary_zh"))
    wechat_brief_limit = max(100, int(os.getenv("PIF_WECHAT_NEWS_MAX_ZH_CHARS", "500")))
    brief_zh_wechat = truncate(
        article.get("wechat_summary_zh") or article.get("content_zh") or article.get("summary_zh"),
        wechat_brief_limit,
    )
    brief_en = clean_space((article.get("analysis") or {}).get("brief_en") or article.get("brief_en"))
    source_news_text = clean_space(article.get("content_original") or article.get("content") or article.get("excerpt"))
    source_news_block = _original_source_block(article, source_news_text, "Original source text")
    elements_zh = (article.get("wechat_elements_zh") if wechat else None) or article.get("elements_zh") or article.get("analysis_zh") or {}
    elements_en = article.get("elements_en") or article.get("analysis_en") or ((article.get("analysis") or {}).get("analysis") or {})
    link = html_escape(article.get("resolved_url") or article.get("url"))
    hide_brief = bool(article.get("wechat_brief_removed"))
    fallback_note = ""
    if wechat:
        brief_html = "" if hide_brief else f'<section style="margin:7px 0;padding:10px 12px;border-radius:6px;background:{COLORS["news_red_bg"]};font-size:15px;line-height:1.75;"><strong style="display:block;margin-bottom:4px;color:{COLORS["news_red"]};">新闻简报</strong>{html_escape(brief_zh_wechat or truncate(brief_en, wechat_brief_limit))}{html_escape(fallback_note)}</section>'
        return f'''<section style="margin:0 0 10px;border:1px solid {COLORS['line']};border-radius:9px;overflow:hidden;background:#fff;"><p style="margin:0;padding:7px 11px;background:{COLORS['panel']};font-size:12px;color:#666;line-height:1.55;"><strong>Published:</strong> {html_escape(article.get('published_date'))} &nbsp;|&nbsp; <strong>Publisher:</strong> {html_escape(article.get('publisher') or article.get('source'))}</p><section style="padding:11px 13px;"><p style="margin:0 0 3px;color:{COLORS['news_red']};font-size:12px;font-weight:bold;">{_tier_badge(article, wechat=True)}公共卫生新闻</p><h3 style="margin:0;color:#1a365d;font-size:18px;line-height:1.45;">{html_escape(title_zh)}</h3><p style="margin:3px 0 5px;color:{COLORS['muted']};font-size:13px;font-style:italic;line-height:1.45;">{html_escape(title_en)}</p>{brief_html}<section style="margin-top:6px;padding:9px 11px;border-left:4px solid {COLORS['amber_line']};background:{COLORS['amber_bg']};font-size:14px;line-height:1.65;"><strong style="color:{COLORS['amber']};">新闻五要素</strong><dl style="margin:5px 0 0;">{_five_elements(elements_zh, _news_fields())}</dl></section></section></section>'''
    return f'''<article class="card news"><div class="meta-strip"><strong>Published:</strong> {html_escape(article.get('published_date'))} &nbsp;|&nbsp; <strong>Publisher:</strong> {html_escape(article.get('publisher') or article.get('source'))}</div><div class="card-body"><div class="lang-zh"><div style="font-size:12px;color:{COLORS['news_red']};font-weight:700;margin-bottom:4px;">{_tier_badge(article)}公共卫生新闻</div><h3>{html_escape(title_zh)}</h3><div class="title-en">{html_escape(title_en)}</div><div class="translated-body"><strong>新闻简报</strong>{html_escape(brief_zh_full or brief_en)}{html_escape(fallback_note)}</div><details><summary>查看新闻五要素</summary><dl class="five-grid">{_five_elements(elements_zh, _news_fields())}</dl></details></div><div class="lang-en" hidden><div style="font-size:12px;color:{COLORS['news_red']};font-weight:700;margin-bottom:4px;">{_tier_badge_en(article)}Public-health news</div><h3>{html_escape(title_en)}</h3>{original_title_block}<div class="original" lang="en"><strong>News Brief</strong><br>{html_escape(brief_en)}</div>{source_news_block}<details><summary>View five news elements</summary><dl class="five-grid">{_five_elements(elements_en, _news_fields_en(), 'Not reported in the supplied evidence.', language='en')}</dl></details></div><div class="links"><a href="{link}">原文 / Source</a></div></div></article>'''


def supplementary_news_card(article: dict[str, Any], *, wechat: bool = False) -> str:
    if wechat and article.get("wechat_omitted"):
        return ""
    raw_title = clean_space(article.get("title_original") or article.get("title"))
    title_en = _english_display_title(article, raw_title, "News article")
    title_zh = clean_space(article.get("title_zh")) or raw_title or title_en
    original_title_block = _original_source_block(article, raw_title, "Original title", metadata_role="title") if raw_title and not is_verified_english(raw_title) else ""
    snippet = "" if article.get("snippet_duplicate_of_title") or (wechat and article.get("wechat_excerpt_removed")) else clean_space(article.get("excerpt") or article.get("content"))
    link = html_escape(article.get("resolved_url") or article.get("url"))
    publisher = clean_space(article.get("publisher") or article.get("source"))
    date = clean_space(article.get("published_date"))
    snippet_zh = clean_space(article.get("excerpt_zh")) or snippet
    scope_notice = _supplementary_scope_notice(article, wechat=wechat)
    if wechat:
        snippet_html = f'<p style="margin:4px 0;font-size:13px;line-height:1.65;color:#586069;">{html_escape(snippet_zh)}</p>' if snippet else ""
        return f'''<section style="margin:0 0 7px;padding:9px 11px;border:1px dashed #d6a3a3;background:#fffafa;border-radius:8px;">{scope_notice}<h3 style="margin:0;color:#7f1d1d;font-size:16px;line-height:1.45;">{html_escape(title_zh)}</h3><p style="margin:2px 0;color:#718096;font-size:12px;font-style:italic;">{html_escape(title_en)}</p><p style="margin:3px 0;font-size:12px;color:#586069;">{html_escape(date)} · {html_escape(publisher)}</p>{snippet_html}</section>'''
    zh_snippet = f'<p style="margin:5px 0;font-size:13px;line-height:1.65;color:#586069;">{html_escape(snippet_zh)}</p>' if snippet else ""
    en_snippet = f'<p style="margin:5px 0;font-size:13px;line-height:1.65;color:#586069;">{html_escape(snippet)}</p>' if snippet else ""
    return f'''<article class="card supplementary-card supplementary news"><div class="meta-strip">{html_escape(date)} &nbsp;|&nbsp; {html_escape(publisher)}</div><div class="card-body">{scope_notice}<div class="lang-zh"><h3>{html_escape(title_zh)}</h3><div class="title-en">{html_escape(title_en)}</div>{zh_snippet}</div><div class="lang-en" hidden><h3>{html_escape(title_en)}</h3>{original_title_block}{en_snippet}</div><div class="links"><a href="{link}">原文 / Source</a></div></div></article>'''


def _overview_html(block: dict[str, Any], title: str, *, wechat: bool = False) -> str:
    if not block:
        return ""
    zh_items = [clean_space(x) for x in block.get("brief_items_zh") or block.get("key_findings_zh") or [] if clean_space(x)][:5]
    en_items = [clean_space(x) for x in block.get("brief_items_en") or block.get("key_findings_en") or [] if clean_space(x)][:5]
    if not zh_items and clean_space(block.get("lead_zh")):
        zh_items = [clean_space(block.get("lead_zh"))]
    if not en_items and clean_space(block.get("lead_en") or block.get("brief_en")):
        en_items = [clean_space(block.get("lead_en") or block.get("brief_en"))]
    if wechat:
        items = "".join(
            f'<li style="margin:4px 0;line-height:1.7;">{html_escape(item)}</li>'
            for item in zh_items
        )
        return (
            f'<section style="padding:14px 20px;background:{COLORS["amber_bg"]};'
            f'border-bottom:3px solid {COLORS["amber_line"]};">'
            f'<h2 style="color:{COLORS["amber"]};margin:0 0 7px;font-size:18px;">{html_escape(title)}</h2>'
            f'<ul style="margin:4px 0;padding-left:20px;font-size:14px;">{items}</ul></section>'
        )
    zh_html = "".join(f"<li>{html_escape(item)}</li>" for item in zh_items)
    en_html = "".join(f"<li>{html_escape(item)}</li>" for item in en_items)
    return (
        f'<section class="overview"><h2>{html_escape(title)}</h2>'
        f'<div class="lang-zh"><ul>{zh_html}</ul></div>'
        f'<div class="lang-en" hidden><ul>{en_html}</ul></div></section>'
    )

def _overview_statlines(issue: dict[str, Any], *, wechat: bool = False) -> str:
    funnel = issue.get("retrieval_funnel") or {}
    papers = funnel.get("papers") or {}
    news = funnel.get("news") or {}
    metrics = issue.get("metrics") or {}
    primary = int(papers.get("primary_displayed") or metrics.get("primary_papers") or metrics.get("papers") or 0)
    supplementary = int(papers.get("supplementary_displayed") or metrics.get("supplementary_papers") or 0)
    main_news = int(news.get("displayed") or metrics.get("news") or 0)
    supplementary_news = int(news.get("supplementary_displayed") or metrics.get("supplementary_news") or 0)
    zh_paper = (
        f"文献：检索{int(papers.get('raw') or 0):,}｜日期窗{int(papers.get('after_window') or 0):,}｜"
        f"去重{int(papers.get('after_dedup') or 0):,}｜终审{int(papers.get('relevant_catalog_after_completion_and_identity_gate') or 0):,}｜"
        f"主报告{primary:,}｜补充{supplementary:,}"
    )
    zh_news = (
        f"新闻：检索{int(news.get('raw') or 0):,}｜日期窗{int(news.get('after_window') or 0):,}｜"
        f"主新闻{main_news:,}｜补充{supplementary_news:,}"
    )
    en_paper = (
        f"Literature: retrieved {int(papers.get('raw') or 0):,} | window {int(papers.get('after_window') or 0):,} | "
        f"deduplicated {int(papers.get('after_dedup') or 0):,} | reviewed {int(papers.get('relevant_catalog_after_completion_and_identity_gate') or 0):,} | "
        f"primary {primary:,} | supplementary {supplementary:,}"
    )
    en_news = (
        f"News: retrieved {int(news.get('raw') or 0):,} | window {int(news.get('after_window') or 0):,} | "
        f"main {main_news:,} | supplementary {supplementary_news:,}"
    )
    if wechat:
        try:
            font_size = max(10, min(16, int(os.getenv("PIF_PUBLIC_OVERVIEW_FONT_SIZE_PX", "12"))))
        except ValueError:
            font_size = 12
        try:
            line_height = max(1.2, min(2.2, float(os.getenv("PIF_PUBLIC_OVERVIEW_LINE_HEIGHT", "1.5"))))
        except ValueError:
            line_height = 1.5
        try:
            margin = max(0, min(24, int(os.getenv("PIF_PUBLIC_OVERVIEW_MARGIN_PX", "8"))))
        except ValueError:
            margin = 8
        color = clean_space(os.getenv("PIF_PUBLIC_OVERVIEW_COLOR", "#888888"))
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            color = "#888888"
        return (
            f'<section style="padding:{margin}px 20px;background:#fff;border-bottom:1px solid #e2e8f0;'
            f'font-size:{font_size}px;line-height:{line_height};font-weight:400;color:{color};">'
            f'<p style="margin:1px 0;">{html_escape(zh_paper)}</p>'
            f'<p style="margin:1px 0;">{html_escape(zh_news)}</p></section>'
        )
    return (
        '<div class="overview-statline">'
        f'<div class="lang-zh"><p>{html_escape(zh_paper)}</p><p>{html_escape(zh_news)}</p></div>'
        f'<div class="lang-en" hidden><p>{html_escape(en_paper)}</p><p>{html_escape(en_news)}</p></div></div>'
    )

def _source_health(issue: dict[str, Any]) -> str:
    rows = ((issue.get("source_status") or {}).get("sources") or [])
    if not rows:
        return ""
    return '<details class="source-health"><summary>Backend source audit</summary><p>' + html_escape('; '.join(f"{x.get('source')}: {x.get('health')}" for x in rows)) + '</p></details>'


def _section(title: str, cls: str, cards: list[str]) -> str:
    return "" if not cards else f'<section><h2 class="section-title {html_escape(cls)}">{html_escape(title)}</h2>{"".join(cards)}</section>'


def render_site(issue: dict[str, Any], output_dir: Path) -> None:
    issue = build_display_issue(issue)
    site_dir = output_dir / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    papers = issue.get("papers") or []
    supplementary = issue.get("supplementary_papers") or []
    news = issue.get("news") or []
    supplementary_news = issue.get("supplementary_news") or []
    research = [p for p in papers if p.get("paper_type") == "research"]
    reviews = [p for p in papers if p.get("paper_type") == "review"]
    overview = issue.get("overview") or {}
    sections = [
        _section("📘 主报告：研究论文 / Primary Research", "research", [paper_card(x) for x in research]),
        _section("📗 主报告：综述 / Primary Reviews", "review", [paper_card(x) for x in reviews]),
        _section("📎 补充文献 / Supplementary Literature", "supplementary", [supplementary_paper_card(x) for x in supplementary]),
        _section("🚨 突发动态与新闻 / Health News", "news", [news_card(x) for x in news]),
        _section("🗂️ 补充新闻 / Supplementary News", "supplementary", [supplementary_news_card(x) for x in supplementary_news]),
    ]
    overview_html = _overview_html(overview.get("literature") or {}, "📚 本期文献进展 / Literature Brief") + _overview_html(overview.get("news") or {}, "📰 本期新闻动态 / News Brief")
    empty_state = ""
    if not papers and not supplementary and not news and not supplementary_news:
        empty_state = (
            '<section class="card empty-state"><div class="card-body">'
            '<p class="lang-zh">本期未发现通过身份、日期与相关性安全门禁的新增文献或新闻。</p>'
            '<p class="lang-en" hidden>No new literature or news passed the identity, date, and relevance safety gates for this issue.</p>'
            '</div></section>'
        )
    html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html_escape(issue['title_zh'])}</title><style>{SITE_CSS}</style></head><body><main class="page"><header class="hero"><img src="assets/cover.jpg" alt="{html_escape(issue['title_zh'])}"><div class="hero-text"><div class="lang-zh"><h1>{html_escape(issue['title_zh'])}</h1><p>{html_escape(issue['issue_date'])} | 文献与公共卫生新闻 | {html_escape(issue['window_start'])}—{html_escape(issue['window_end'])}</p></div><div class="lang-en" hidden><h1>{html_escape(issue['title_en'])}</h1><p>{html_escape(issue['issue_date'])} | Literature and public-health news | {html_escape(issue['window_start'])}—{html_escape(issue['window_end'])}</p></div></div></header>{_overview_statlines(issue)}{overview_html}<div class="toolbar"><button class="language-toggle" data-language="zh">中文</button><button class="language-toggle" data-language="en">English</button></div><div class="stats"><div><strong>{len(research)}</strong><span class="lang-zh">主报告研究</span><span class="lang-en" hidden>Primary research</span></div><div><strong>{len(reviews)}</strong><span class="lang-zh">主报告综述</span><span class="lang-en" hidden>Primary reviews</span></div><div><strong>{len(supplementary)}</strong><span class="lang-zh">补充文献</span><span class="lang-en" hidden>Supplementary literature</span></div><div><strong>{len(news)}</strong><span class="lang-zh">主新闻</span><span class="lang-en" hidden>Main news</span></div><div><strong>{len(supplementary_news)}</strong><span class="lang-zh">补充新闻</span><span class="lang-en" hidden>Supplementary news</span></div><div><strong>{issue.get('metrics',{}).get('translated',0)}</strong><span class="lang-zh">深度双语记录</span><span class="lang-en" hidden>Deep bilingual records</span></div></div><div class="content">{empty_state}{"".join(sections)}</div><footer><span class="lang-zh">病原文献与新闻情报</span><span class="lang-en" hidden>Pathogen literature and news intelligence</span></footer></main><script>{SITE_JS}</script></body></html>'''
    (site_dir / "index.html").write_text(html, encoding="utf-8")
    items: list[str] = []
    for item in papers[:10] + news[:10]:
        title = item.get("title_zh") or item.get("title")
        link = f"https://doi.org/{item.get('doi')}" if item.get("doi") else item.get("resolved_url") or item.get("url") or ""
        description = item.get("abstract_zh") or item.get("content_zh") or item.get("summary_zh") or ""
        items.append(f"<item><title>{html_escape(title)}</title><link>{html_escape(link)}</link><description>{html_escape(description)}</description></item>")
    feed = f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{html_escape(issue["title_zh"])}</title><link>./</link><description>{html_escape(issue["title_en"])}</description>{"".join(items)}</channel></rss>'
    (site_dir / "feed.xml").write_text(feed, encoding="utf-8")


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.parts.append(text)


def visible_text_count(html: str) -> int:
    parser = _VisibleTextParser()
    parser.feed(html)
    return len("".join(parser.parts))


def _truncate_with_audit(value: Any, limit: int) -> tuple[str, bool]:
    text = clean_space(value)
    if not text or limit <= 0 or len(text) <= limit:
        return text, False
    return truncate(text, limit), True


def _prepare_wechat_display_copy(working: dict[str, Any]) -> dict[str, int]:
    limits = {
        "title": max(40, int(os.getenv("PIF_WECHAT_TITLE_MAX_CHARS", "220"))),
        "authors": max(60, int(os.getenv("PIF_WECHAT_AUTHORS_MAX_CHARS", "300"))),
        "paper_abstract": max(100, int(os.getenv("PIF_WECHAT_PAPER_ABSTRACT_MAX_CHARS", "500"))),
        "paper_element": max(60, int(os.getenv("PIF_WECHAT_PAPER_ELEMENT_MAX_CHARS", "120"))),
        "news_element": max(60, int(os.getenv("PIF_WECHAT_NEWS_ELEMENT_MAX_CHARS", "100"))),
        "overview": max(200, int(os.getenv("PIF_WECHAT_OVERVIEW_MAX_CHARS", "1200"))),
    }
    counts = {"titles": 0, "authors": 0, "paper_abstracts": 0, "paper_elements": 0, "news_elements": 0, "overviews": 0}
    for key in ("papers", "supplementary_papers", "news", "supplementary_news"):
        for record in working.get(key) or []:
            for src, dst in (("title", "wechat_title_en"), ("title_zh", "wechat_title_zh")):
                value, cut = _truncate_with_audit(record.get(src), limits["title"])
                record[dst] = value
                counts["titles"] += int(cut)
    for record in working.get("papers") or []:
        authors = ", ".join((record.get("authors") or [])[:10]) or "Authors unavailable"
        record["wechat_authors"], cut = _truncate_with_audit(authors, limits["authors"])
        counts["authors"] += int(cut)
        record["wechat_abstract_zh"], cut = _truncate_with_audit(record.get("abstract_zh") or record.get("summary_zh"), limits["paper_abstract"])
        counts["paper_abstracts"] += int(cut)
        elements = record.get("elements_zh") or record.get("analysis_zh") or {}
        compact: dict[str, str] = {}
        for name, value in elements.items():
            compact[name], cut = _truncate_with_audit(value, limits["paper_element"])
            counts["paper_elements"] += int(cut)
        record["wechat_elements_zh"] = compact
    for record in working.get("news") or []:
        elements = record.get("elements_zh") or record.get("analysis_zh") or {}
        compact: dict[str, str] = {}
        for name, value in elements.items():
            compact[name], cut = _truncate_with_audit(value, limits["news_element"])
            counts["news_elements"] += int(cut)
        record["wechat_elements_zh"] = compact
    overview = working.get("overview") or {}
    for section in ("literature", "news"):
        block = overview.get(section) or {}
        if isinstance(block, dict) and block.get("zh"):
            block["zh"], cut = _truncate_with_audit(block.get("zh"), limits["overview"])
            counts["overviews"] += int(cut)
    working["_wechat_field_limits"] = {"limits": limits, "truncated_fields": counts}
    return counts


def _wechat_budget_state(working: dict[str, Any], original: dict[str, Any]) -> dict[str, Any]:
    papers = working.get("papers") or []
    supplementary = working.get("supplementary_papers") or []
    news = working.get("news") or []
    supplementary_news = working.get("supplementary_news") or []
    state = {
        "primary_papers_total": len(original.get("papers") or []),
        "primary_papers_displayed": sum(not bool(x.get("wechat_omitted")) for x in papers),
        "primary_papers_omitted": sum(bool(x.get("wechat_omitted")) for x in papers),
        "primary_papers_compacted": sum(bool(x.get("wechat_compact_details_removed")) and not bool(x.get("wechat_omitted")) for x in papers),
        "supplementary_papers_total": len(original.get("supplementary_papers") or []),
        "supplementary_papers_displayed": sum(not bool(x.get("wechat_omitted")) for x in supplementary),
        "supplementary_papers_omitted": sum(bool(x.get("wechat_omitted")) for x in supplementary),
        "main_news_total": len(original.get("news") or []),
        "main_news_displayed": sum(not bool(x.get("wechat_omitted")) for x in news),
        "main_news_omitted": sum(bool(x.get("wechat_omitted")) for x in news),
        "main_news_briefs_removed": sum(bool(x.get("wechat_brief_removed")) for x in news),
        "supplementary_news_total": len(original.get("supplementary_news") or []),
        "supplementary_news_displayed": sum(not bool(x.get("wechat_omitted")) for x in supplementary_news),
        "supplementary_news_omitted": sum(bool(x.get("wechat_omitted")) for x in supplementary_news),
        "supplementary_news_excerpts_removed": sum(bool(x.get("wechat_excerpt_removed")) for x in supplementary_news),
    }
    state["notice_required"] = any(
        int(state[key]) > 0
        for key in (
            "primary_papers_compacted",
            "primary_papers_omitted",
            "main_news_briefs_removed",
            "supplementary_news_excerpts_removed",
            "supplementary_papers_omitted",
            "supplementary_news_omitted",
            "main_news_omitted",
        )
    )
    working["_wechat_budget"] = state
    return state


def _wechat_budget_notice(issue: dict[str, Any]) -> str:
    del issue
    return ""

def _wechat_body(issue: dict[str, Any], source_url: str) -> str:
    papers_all = issue.get("papers") or []
    papers = [x for x in papers_all if not x.get("wechat_omitted")]
    supplementary_all = issue.get("supplementary_papers") or []
    news_all = issue.get("news") or []
    supplementary_news_all = issue.get("supplementary_news") or []
    supplementary = [x for x in supplementary_all if not x.get("wechat_omitted")]
    news = [x for x in news_all if not x.get("wechat_omitted")]
    supplementary_news = [x for x in supplementary_news_all if not x.get("wechat_omitted")]
    overview = issue.get("overview") or {}
    supplementary_html = "".join(supplementary_paper_card(x, wechat=True) for x in supplementary)
    supplementary_news_html = "".join(supplementary_news_card(x, wechat=True) for x in supplementary_news)
    source_link = f'<p style="margin:8px 0;text-align:right;font-weight:700;"><a href="{html_escape(source_url)}">查看完整网页</a></p>' if source_url else ""
    primary_heading = "📘 主报告文献"
    supp_heading = "📎 补充文献目录"
    supp_news_heading = "🗂️ 补充新闻"
    main_news_heading = "🚨 突发动态与新闻"
    supplementary_section = (
        f'<h2 style="margin:15px 0 8px;border-left:6px solid #a0aec0;padding-left:10px;color:#4a5568;font-size:20px;">{html_escape(supp_heading)}</h2>{supplementary_html}'
        if supplementary_html else ""
    )
    supplementary_news_section = (
        f'<h2 style="margin:15px 0 8px;border-left:6px solid #a0aec0;padding-left:10px;color:#4a5568;font-size:20px;">{html_escape(supp_news_heading)}</h2>{supplementary_news_html}'
        if supplementary_news_html else ""
    )
    paper_html = "".join(paper_card(x, wechat=True) for x in papers) or '<p>本期无满足主报告证据标准的文献。</p>'
    news_html = "".join(news_card(x, wechat=True) for x in news) or '<p>本期无满足身份、正文与相关性标准的新闻。</p>'
    return f'''<section style="font-family:Arial,'Noto Sans CJK SC',sans-serif;color:#333;line-height:1.75;"><section style="padding:17px 20px;background:{COLORS['navy']};color:#fff;text-align:center;"><h1 style="margin:0;font-size:24px;">{html_escape(issue['title_zh'])}</h1><p style="margin:5px 0 0;font-size:13px;opacity:.85;">{html_escape(issue['issue_date'])} | 文献与公共卫生新闻</p></section>{_overview_statlines(issue, wechat=True)}{_wechat_budget_notice(issue)}{_overview_html(overview.get('literature') or {}, '📚 本期文献进展', wechat=True)}{_overview_html(overview.get('news') or {}, '📰 本期新闻动态', wechat=True)}<h2 style="margin:15px 0 8px;border-left:6px solid {COLORS['paper_green']};padding-left:10px;color:{COLORS['paper_green']};font-size:20px;">{html_escape(primary_heading)}</h2>{paper_html}{supplementary_section}<h2 style="margin:15px 0 8px;border-left:6px solid {COLORS['news_red']};padding-left:10px;color:{COLORS['news_red']};font-size:20px;">{html_escape(main_news_heading)}</h2>{news_html}{supplementary_news_section}{source_link}</section>'''


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return clean_space(raw).lower() in {"1", "true", "yes", "on"}


def render_wechat_package(issue: dict[str, Any], output_dir: Path, cover_meta: dict[str, Any]) -> dict[str, Any]:
    issue = build_display_issue(issue)
    package = output_dir / "wechat-package"
    package.mkdir(parents=True, exist_ok=True)
    source_url = os.getenv("PIF_CONTENT_SOURCE_URL", "").strip()
    max_chars = max(1000, int(os.getenv("PIF_WECHAT_MAX_VISIBLE_CHARS", "48000")))
    min_full = max(0, int(os.getenv("PIF_WECHAT_MIN_FULL_PAPERS", "10")))
    remove_supplementary_news_excerpts = _env_bool("PIF_WECHAT_REMOVE_SUPPLEMENTARY_NEWS_EXCERPTS", True)
    allow_supplementary_paper_omission = _env_bool("PIF_WECHAT_ALLOW_SUPPLEMENTARY_PAPER_OMISSION", True)
    min_supplementary_papers = max(0, int(os.getenv("PIF_WECHAT_MIN_SUPPLEMENTARY_PAPERS", "0")))
    allow_supplementary_news_omission = _env_bool("PIF_WECHAT_ALLOW_SUPPLEMENTARY_NEWS_OMISSION", True)
    min_supplementary_news = max(0, int(os.getenv("PIF_WECHAT_MIN_SUPPLEMENTARY_NEWS", "0")))
    allow_main_news_omission = _env_bool("PIF_WECHAT_ALLOW_MAIN_NEWS_OMISSION", True)
    min_main_news = max(0, int(os.getenv("PIF_WECHAT_MIN_MAIN_NEWS", "10")))
    allow_primary_paper_omission = _env_bool("PIF_WECHAT_ALLOW_PRIMARY_PAPER_OMISSION", True)
    min_primary_papers = max(min_full, int(os.getenv("PIF_WECHAT_MIN_PRIMARY_PAPERS", str(min_full))))

    working = copy.deepcopy(issue)
    field_truncation_counts = _prepare_wechat_display_copy(working)
    steps: list[dict[str, Any]] = []

    def rerender() -> tuple[str, int]:
        _wechat_budget_state(working, issue)
        current = _wechat_body(working, source_url)
        return current, visible_text_count(current)

    def record_step(action: str, record_id: str, old_count: int, new_count: int) -> None:
        steps.append({
            "action": action,
            "record_id": record_id,
            "visible_chars_before": old_count,
            "visible_chars_after": new_count,
            "saved_chars": max(0, old_count - new_count),
        })

    body, before = rerender()
    current_count = before
    compacted_paper_ids: list[str] = []
    removed_news_brief_ids: list[str] = []
    removed_supplementary_news_excerpt_ids: list[str] = []
    omitted_supplementary_paper_ids: list[str] = []
    omitted_supplementary_news_ids: list[str] = []
    omitted_main_news_ids: list[str] = []
    omitted_primary_paper_ids: list[str] = []

    if current_count > max_chars:
        papers = working.get("papers") or []
        for index in range(len(papers) - 1, min_full - 1, -1):
            record_id = clean_space(papers[index].get("paper_id")) or f"paper-index-{index}"
            old_count = current_count
            papers[index]["wechat_compact_details_removed"] = True
            compacted_paper_ids.append(record_id)
            body, current_count = rerender()
            record_step("compact_primary_paper_details", record_id, old_count, current_count)
            if current_count <= max_chars:
                break

    if current_count > max_chars:
        news = working.get("news") or []
        for index in range(len(news) - 1, -1, -1):
            record_id = clean_space(news[index].get("news_id")) or f"news-index-{index}"
            old_count = current_count
            news[index]["wechat_brief_removed"] = True
            removed_news_brief_ids.append(record_id)
            body, current_count = rerender()
            record_step("remove_main_news_brief", record_id, old_count, current_count)
            if current_count <= max_chars:
                break

    if current_count > max_chars and remove_supplementary_news_excerpts:
        supplementary_news = working.get("supplementary_news") or []
        for index in range(len(supplementary_news) - 1, -1, -1):
            if supplementary_news[index].get("snippet_duplicate_of_title"):
                continue
            has_excerpt = bool(clean_space(supplementary_news[index].get("excerpt") or supplementary_news[index].get("content")))
            if not has_excerpt:
                continue
            record_id = clean_space(supplementary_news[index].get("news_id")) or f"supp-news-index-{index}"
            old_count = current_count
            supplementary_news[index]["wechat_excerpt_removed"] = True
            removed_supplementary_news_excerpt_ids.append(record_id)
            body, current_count = rerender()
            record_step("remove_supplementary_news_excerpt", record_id, old_count, current_count)
            if current_count <= max_chars:
                break

    if current_count > max_chars and allow_supplementary_paper_omission:
        supplementary = working.get("supplementary_papers") or []
        stop_index = min(len(supplementary), min_supplementary_papers)
        for index in range(len(supplementary) - 1, stop_index - 1, -1):
            record_id = clean_space(supplementary[index].get("paper_id")) or f"supp-paper-index-{index}"
            old_count = current_count
            supplementary[index]["wechat_omitted"] = True
            omitted_supplementary_paper_ids.append(record_id)
            body, current_count = rerender()
            record_step("omit_supplementary_paper_card", record_id, old_count, current_count)
            if current_count <= max_chars:
                break

    if current_count > max_chars and allow_supplementary_news_omission:
        supplementary_news = working.get("supplementary_news") or []
        stop_index = min(len(supplementary_news), min_supplementary_news)
        for index in range(len(supplementary_news) - 1, stop_index - 1, -1):
            if supplementary_news[index].get("wechat_omitted"):
                continue
            record_id = clean_space(supplementary_news[index].get("news_id")) or f"supp-news-index-{index}"
            old_count = current_count
            supplementary_news[index]["wechat_omitted"] = True
            omitted_supplementary_news_ids.append(record_id)
            body, current_count = rerender()
            record_step("omit_supplementary_news_card", record_id, old_count, current_count)
            if current_count <= max_chars:
                break

    if current_count > max_chars and allow_main_news_omission:
        news = working.get("news") or []
        stop_index = min(len(news), min_main_news)
        for index in range(len(news) - 1, stop_index - 1, -1):
            if news[index].get("wechat_omitted"):
                continue
            record_id = clean_space(news[index].get("news_id")) or f"news-index-{index}"
            old_count = current_count
            news[index]["wechat_omitted"] = True
            omitted_main_news_ids.append(record_id)
            body, current_count = rerender()
            record_step("omit_main_news_card_emergency", record_id, old_count, current_count)
            if current_count <= max_chars:
                break

    if current_count > max_chars and allow_primary_paper_omission:
        papers = working.get("papers") or []
        stop_index = min(len(papers), min_primary_papers)
        for index in range(len(papers) - 1, stop_index - 1, -1):
            if papers[index].get("wechat_omitted"):
                continue
            record_id = clean_space(papers[index].get("paper_id")) or f"paper-index-{index}"
            old_count = current_count
            papers[index]["wechat_omitted"] = True
            omitted_primary_paper_ids.append(record_id)
            body, current_count = rerender()
            record_step("omit_primary_paper_card_emergency", record_id, old_count, current_count)
            if current_count <= max_chars:
                break

    state = _wechat_budget_state(working, issue)
    body = _wechat_body(working, source_url)
    after = visible_text_count(body)
    if after > max_chars:
        raise RuntimeError(
            "wechat_budget_unresolvable: visible text remains above hard budget after all configured "
            f"fallback stages: {after}>{max_chars}; minimum_full_papers={min_full}, "
            f"minimum_primary_papers={min_primary_papers}, minimum_main_news={min_main_news}"
        )

    audit = {
        "policy_version": "v17-wechat-visible-text-budget-audit-only-1",
        "max_visible_chars": max_chars,
        "minimum_full_papers": min_full,
        "minimum_primary_papers": min_primary_papers,
        "field_display_limits": (working.get("_wechat_field_limits") or {}).get("limits") or {},
        "truncated_display_fields": field_truncation_counts,
        "minimum_supplementary_papers": min_supplementary_papers,
        "minimum_supplementary_news": min_supplementary_news,
        "minimum_main_news": min_main_news,
        "visible_chars_before": before,
        "visible_chars_after": after,
        "within_budget": after <= max_chars,
        "compaction_steps": steps,
        "compacted_primary_papers": len(compacted_paper_ids),
        "compacted_primary_paper_ids": compacted_paper_ids,
        "primary_papers_total": len(issue.get("papers") or []),
        "primary_papers_displayed": sum(not bool(x.get("wechat_omitted")) for x in working.get("papers") or []),
        "primary_papers_omitted": len(omitted_primary_paper_ids),
        "omitted_primary_paper_ids": omitted_primary_paper_ids,
        "removed_main_news_briefs": len(removed_news_brief_ids),
        "removed_main_news_brief_ids": removed_news_brief_ids,
        "removed_supplementary_news_excerpts": len(removed_supplementary_news_excerpt_ids),
        "removed_supplementary_news_excerpt_ids": removed_supplementary_news_excerpt_ids,
        "supplementary_papers_total": state["supplementary_papers_total"],
        "supplementary_papers_displayed": state["supplementary_papers_displayed"],
        "supplementary_papers_omitted": state["supplementary_papers_omitted"],
        "supplementary_paper_ids_omitted": omitted_supplementary_paper_ids,
        "supplementary_news_total": state["supplementary_news_total"],
        "supplementary_news_displayed": state["supplementary_news_displayed"],
        "supplementary_news_omitted": state["supplementary_news_omitted"],
        "supplementary_news_ids_omitted": omitted_supplementary_news_ids,
        "main_news_total": state["main_news_total"],
        "main_news_displayed": state["main_news_displayed"],
        "main_news_omitted": state["main_news_omitted"],
        "main_news_ids_omitted": omitted_main_news_ids,
        "budget_notice_rendered": False,
        "operational_notice_rendered": False,
        "full_catalog_preserved_in_source_data": True,
        "supplementary_literature_preserved": len(issue.get("supplementary_papers") or []),
        "supplementary_news_preserved": len(issue.get("supplementary_news") or []),
        "main_news_elements_preserved": True,
        "main_news_elements_preserved_for_displayed_cards": True,
    }
    (package / "article.html").write_text(body, encoding="utf-8")
    overview = issue.get("overview") or {}
    digest_source = clean_space((overview.get("literature") or {}).get("lead_zh") or (overview.get("literature") or {}).get("headline_zh") or (overview.get("news") or {}).get("lead_zh") or issue.get("title_zh"))
    manifest = {
        "schema_version": 2, "contract": "pathogen-wechat-package/v2", "publish_key": issue["issue_id"],
        "profile_id": issue["profile_id"], "report_date": issue["issue_date"],
        "title": f"{issue['title_zh']}｜{issue['issue_date']}", "digest": digest_source[:120],
        "content_file": "article.html", "content_source_url": source_url, "show_cover_pic": 1,
        "need_open_comment": 0, "only_fans_can_comment": 0, "images": [],
        "cover": {"file": "cover.jpg", "sha256": cover_meta.get("cover_sha256"), "asset_key": issue["profile_id"], "generator": cover_meta.get("generator"), "profile_fingerprint": cover_meta.get("profile_fingerprint")},
        "source": {
            "profile_id": issue["profile_id"], "issue_id": issue["issue_id"], "generated_at": issue["generated_at"],
            "issue_schema_version": issue.get("schema_version"),
            "primary_papers": len(issue.get("papers") or []),
            "primary_papers_displayed_wechat": audit["primary_papers_displayed"],
            "primary_papers_omitted_wechat": audit["primary_papers_omitted"],
            "supplementary_papers": len(issue.get("supplementary_papers") or []),
            "supplementary_papers_displayed_wechat": state["supplementary_papers_displayed"],
            "supplementary_papers_omitted_wechat": state["supplementary_papers_omitted"],
            "news": len(issue.get("news") or []),
            "news_displayed_wechat": state["main_news_displayed"],
            "news_omitted_wechat": state["main_news_omitted"],
            "supplementary_news": len(issue.get("supplementary_news") or []),
            "supplementary_news_displayed_wechat": state["supplementary_news_displayed"],
            "supplementary_news_omitted_wechat": state["supplementary_news_omitted"],
            "wechat_visible_chars": after,
            "wechat_budget_policy_version": audit["policy_version"],
        },
    }
    dump_json(package / "manifest.json", manifest)
    dump_json(package / "content-budget-audit.json", audit)
    return audit
