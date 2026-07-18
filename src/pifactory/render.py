from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .utils import clean_space, dump_json, html_escape, sha256_text

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
    "ink": "#333333",
    "muted": "#718096",
    "line": "#e2e8f0",
    "panel": "#f8fafc",
}

SITE_CSS = """
:root{--navy:#2c3e50;--green:#27ae60;--red:#c53030;--amber:#c05621;--line:#e2e8f0;--muted:#718096}
*{box-sizing:border-box}body{margin:0;background:#f4f7f9;color:#333;font-family:Arial,'Noto Sans CJK SC',sans-serif}a{color:#0366d6;text-decoration:none}.page{max-width:1040px;margin:24px auto;background:#fff;border-radius:15px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.1)}.hero{position:relative;background:var(--navy);color:white;text-align:center}.hero img{width:100%;display:block;max-height:442px;object-fit:cover}.hero-text{padding:20px 30px 26px}.hero h1{margin:0;font-size:30px}.hero p{margin:8px 0 0;opacity:.82}.overview{padding:25px;background:#fffcf0;border-bottom:5px solid #fbd38d}.overview h2{color:var(--amber);margin:0 0 12px;font-size:19px}.overview p{margin:7px 0;line-height:1.75}.stats{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:1px solid var(--line)}.stats div{padding:16px;text-align:center;border-right:1px solid var(--line)}.stats div:last-child{border-right:0}.stats strong{font-size:27px;color:var(--red);display:block}.content{padding:30px}section{margin-top:34px}section:first-child{margin-top:0}.section-title{font-size:22px;padding-left:15px;margin:0 0 18px;border-left:6px solid}.section-title.research,.section-title.review{color:var(--green);border-color:var(--green)}.section-title.news{color:var(--red);border-color:var(--red)}.card{margin-bottom:28px;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:white}.meta-strip{padding:10px 15px;background:#f8fafc;border-bottom:1px solid var(--line);font-size:12px;color:#666;line-height:1.65}.card-body{padding:20px}.card h3{font-size:19px;color:#1a365d;line-height:1.45;margin:0}.title-en{font-size:14px;color:var(--muted);font-style:italic;margin-top:6px;line-height:1.5}.authors{font-size:13px;color:#586069;margin:10px 0}.translated-body{font-size:15px;line-height:1.75;margin:15px 0;padding:15px;border-radius:6px;background:#f0fff4}.news .translated-body{background:#fff5f5}.translated-body strong{display:block;margin-bottom:6px;color:#1e7e34}.news .translated-body strong{color:var(--red)}details{margin-top:12px;border-top:1px dotted var(--line);border-bottom:1px dotted var(--line);padding:9px 0}summary{cursor:pointer;font-weight:700;color:var(--amber)}.five-grid{display:grid;grid-template-columns:88px 1fr;gap:7px 10px;margin-top:10px;font-size:14px;line-height:1.55}.five-grid dt{font-weight:700;color:var(--amber)}.five-grid dd{margin:0}.original{font-size:13px;line-height:1.65;color:#666;background:#f8fafc;padding:12px;margin-top:10px;border-radius:6px}.links{text-align:right;font-size:13px;margin-top:14px;font-weight:700}.tier-badge{display:inline-block;padding:2px 7px;border-radius:999px;font-size:11px;font-weight:700;margin-right:6px}.tier-A{background:#e6fffa;color:#06735f}.tier-B{background:#ebf8ff;color:#2b6cb0}.tier-C{background:#f7fafc;color:#718096;border:1px solid #e2e8f0}.audit{margin-top:8px;color:#a0aec0;font-size:11px}.toolbar{display:flex;justify-content:flex-end;gap:8px;padding:12px 30px;border-bottom:1px solid var(--line)}button{font:inherit;border:1px solid var(--line);background:white;padding:6px 10px;cursor:pointer}footer{background:var(--navy);color:white;padding:20px;text-align:center;font-size:11px;line-height:1.6}[hidden]{display:none!important}@media(max-width:700px){.page{margin:0;border-radius:0}.content{padding:18px}.stats{grid-template-columns:repeat(2,1fr)}.five-grid{grid-template-columns:70px 1fr}.toolbar{justify-content:center;padding:10px}}
"""

SITE_JS = r"""
document.querySelectorAll('[data-language]').forEach(button=>button.addEventListener('click',()=>{
 const lang=button.dataset.language;
 document.querySelectorAll('.lang-zh').forEach(x=>x.hidden=lang!=='zh');
 document.querySelectorAll('.lang-en').forEach(x=>x.hidden=lang!=='en');
}));
"""


def _date_label(label: str, value: Any) -> str | None:
    value = str(value or "").strip()
    return f"<strong>{html_escape(label)}:</strong> {html_escape(value)}" if value else None


def _paper_meta(work: dict[str, Any]) -> str:
    items = [
        _date_label("Journal", work.get("journal") or "Unknown Journal"),
        _date_label("Online", work.get("online_date")),
        _date_label("First published", work.get("first_publication_date")),
        _date_label("Print", work.get("print_date")),
        _date_label("Database created", work.get("created_date")),
        _date_label("Report date", work.get("availability_date")),
        _date_label("DOI", work.get("doi")),
    ]
    bib = ", ".join(
        str(x)
        for x in [work.get("year"), work.get("volume"), work.get("issue"), work.get("pages")]
        if x not in (None, "")
    )
    if bib:
        items.append(_date_label("Citation", bib))
    return " &nbsp;|&nbsp; ".join(item for item in items if item)


def _paper_fields(kind: str) -> list[tuple[str, str]]:
    if kind == "review":
        return [
            ("背景", "background"),
            ("主要方向", "main_directions"),
            ("研究现状", "current_state"),
            ("不足", "gaps"),
            ("后续研究", "future_research"),
        ]
    return [
        ("背景", "background"),
        ("方法", "methods"),
        ("结果", "results"),
        ("贡献", "contribution"),
        ("局限", "limitations"),
    ]


def _five_elements(data: dict[str, Any], fields: list[tuple[str, str]]) -> str:
    return "".join(
        f"<dt>{html_escape(label)}</dt><dd>{html_escape(data.get(key) or '未报告')}</dd>"
        for label, key in fields
    )


def _attempt_label(audit: dict[str, Any]) -> str:
    return html_escape(
        f"{audit.get('status') or 'unknown'} / {audit.get('provider') or 'unknown'}"
    )


def _tier_badge(item: dict[str, Any], *, wechat: bool = False) -> str:
    tier = str(item.get("priority_tier") or "C").upper()
    label = {"A": "A 高优先级", "B": "B 常规重要", "C": "C 补充信息"}.get(tier, "C 补充信息")
    title = html_escape(item.get("priority_tier_reason") or "")
    if wechat:
        bg = {"A": "#e6fffa", "B": "#ebf8ff", "C": "#f7fafc"}.get(tier, "#f7fafc")
        fg = {"A": "#06735f", "B": "#2b6cb0", "C": "#718096"}.get(tier, "#718096")
        return f'<span title="{title}" style="display:inline-block;padding:2px 7px;border-radius:999px;background:{bg};color:{fg};font-size:11px;font-weight:700;margin-right:6px;">{label}</span>'
    return f'<span class="tier-badge tier-{html_escape(tier)}" title="{title}">{label}</span>'


def paper_card(work: dict[str, Any], *, wechat: bool = False) -> str:
    kind = work.get("paper_type") or "research"
    title_zh = work.get("title_zh") or "中文标题翻译暂不可用"
    translated = work.get("abstract_zh") or work.get("summary_zh") or "原始记录未提供可翻译摘要；本卡片不根据标题编造研究发现。"
    original = work.get("abstract") or "Original abstract is unavailable."
    authors = ", ".join((work.get("authors") or [])[:10]) or "Authors unavailable"
    analysis = work.get("analysis_zh") or {}
    audit = work.get("translation_audit") or {}
    links = []
    if work.get("doi"):
        links.append(f'<a href="https://doi.org/{html_escape(work["doi"])}">DOI</a>')
    pmid = (work.get("source_ids") or {}).get("pmid")
    if pmid:
        links.append(f'<a href="https://pubmed.ncbi.nlm.nih.gov/{html_escape(pmid)}/">PubMed</a>')
    if work.get("full_text_url"):
        links.append(f'<a href="{html_escape(work["full_text_url"])}">开放正文</a>')
    elif work.get("url"):
        links.append(f'<a href="{html_escape(work["url"])}">来源</a>')

    if wechat:
        return f"""
<section style="margin:0 0 28px;border:1px solid {COLORS['line']};border-radius:10px;overflow:hidden;background:#fff;">
  <p style="margin:0;padding:10px 14px;background:{COLORS['panel']};font-size:12px;color:#666;line-height:1.65;">{_paper_meta(work)}</p>
  <section style="padding:18px;">
    <p style="margin:0 0 5px;color:{COLORS['paper_green']};font-size:12px;font-weight:bold;">{_tier_badge(work, wechat=True)}学术文献 · {'综述' if kind == 'review' else '研究'} · {html_escape(work.get('evidence_level') or 'E0')}</p>
    <h3 style="margin:0;color:#1a365d;font-size:19px;line-height:1.45;">{html_escape(title_zh)}</h3>
    <p style="margin:6px 0 10px;color:{COLORS['muted']};font-size:13px;font-style:italic;line-height:1.5;">{html_escape(work.get('title'))}</p>
    <p style="margin:8px 0;color:#586069;font-size:13px;"><strong>Authors:</strong> {html_escape(authors)}</p>
    <section style="margin:14px 0;padding:14px;border-radius:6px;background:{COLORS['paper_green_bg']};font-size:15px;line-height:1.75;"><strong style="display:block;margin-bottom:6px;color:{COLORS['paper_green_dark']};">摘要中文翻译</strong>{html_escape(translated)}</section>
    <section style="margin-top:12px;padding:12px 14px;border-left:4px solid {COLORS['amber_line']};background:{COLORS['amber_bg']};font-size:14px;line-height:1.65;"><strong style="color:{COLORS['amber']};">五要素解读</strong><dl>{_five_elements(analysis, _paper_fields(kind))}</dl></section>
    <p style="margin-top:13px;text-align:right;font-size:13px;font-weight:bold;">{' · '.join(links)}</p>
  </section>
</section>"""

    return f"""
<article class="card paper">
  <div class="meta-strip">{_paper_meta(work)}</div>
  <div class="card-body">
    <div style="font-size:12px;color:{COLORS['paper_green']};font-weight:700;margin-bottom:7px;">{_tier_badge(work)}学术文献 · {'综述' if kind == 'review' else '研究'} · {html_escape(work.get('evidence_level') or 'E0')}</div>
    <div class="lang-zh"><h3>{html_escape(title_zh)}</h3><div class="title-en">{html_escape(work.get('title'))}</div><div class="authors"><strong>Authors:</strong> {html_escape(authors)}</div><div class="translated-body"><strong>摘要中文翻译</strong>{html_escape(translated)}</div></div>
    <div class="lang-en" hidden><h3>{html_escape(work.get('title'))}</h3><div class="authors"><strong>Authors:</strong> {html_escape(authors)}</div><div class="original"><strong>Original Abstract</strong><br>{html_escape(original)}</div></div>
    <details><summary>查看五要素解读</summary><dl class="five-grid">{_five_elements(analysis, _paper_fields(kind))}</dl></details>
    <div class="links">{' · '.join(links)}</div>
    <div class="audit">标题翻译：{_attempt_label(audit.get('title') or {})}；摘要翻译：{_attempt_label(audit.get('abstract_or_body') or {})}；分析：{html_escape((work.get('analysis') or {}).get('status') or 'unknown')}</div>
  </div>
</article>"""


def news_card(article: dict[str, Any], *, wechat: bool = False) -> str:
    title_zh = article.get("title_zh") or "中文标题翻译暂不可用"
    translated = article.get("content_zh") or article.get("summary_zh") or "未抓获可分析正文，当前仅保留标题、来源与发布时间；不根据标题扩写新闻内容。"
    original = article.get("content") or article.get("excerpt") or "Original news body is unavailable."
    fields = [("时间", "time"), ("地点", "location"), ("事件", "event"), ("影响", "impact"), ("状态", "status")]
    analysis = article.get("analysis_zh") or {}
    link = html_escape(article.get("resolved_url") or article.get("url"))
    meta = " &nbsp;|&nbsp; ".join(
        x for x in [
            _date_label("Source", article.get("publisher") or article.get("source")),
            _date_label("Published", article.get("published_date")),
            _date_label("Fetched", article.get("retrieved_at")),
        ] if x
    )
    if wechat:
        return f"""
<section style="margin:0 0 20px;border:1px solid #fed7d7;border-radius:9px;overflow:hidden;background:#fff;">
  <p style="margin:0;padding:10px 14px;background:{COLORS['news_red_bg']};font-size:12px;color:#666;line-height:1.65;">{meta}</p>
  <section style="padding:17px;">
    <p style="margin:0 0 6px;">{_tier_badge(article, wechat=True)}</p><h3 style="margin:0;color:#9b2c2c;font-size:18px;line-height:1.45;">{html_escape(title_zh)}</h3>
    <p style="margin:5px 0;color:{COLORS['muted']};font-size:13px;line-height:1.5;">{html_escape(article.get('title'))}</p>
    <section style="margin:13px 0;padding:14px;border-radius:6px;background:{COLORS['news_red_bg']};font-size:15px;line-height:1.75;"><strong style="display:block;margin-bottom:6px;color:{COLORS['news_red']};">新闻正文/简要中文翻译</strong>{html_escape(translated)}</section>
    <section style="padding:12px 14px;border-left:4px solid {COLORS['amber_line']};background:{COLORS['amber_bg']};font-size:14px;line-height:1.65;"><strong style="color:{COLORS['amber']};">新闻五要素</strong><dl>{_five_elements(analysis, fields)}</dl></section>
    <p style="text-align:right;font-size:13px;font-weight:bold;"><a href="{link}">查看原始报道</a></p>
  </section>
</section>"""
    audit = article.get("translation_audit") or {}
    return f"""
<article class="card news">
  <div class="meta-strip">{meta}</div>
  <div class="card-body">
    <div class="lang-zh">{_tier_badge(article)}<h3 style="color:#9b2c2c">{html_escape(title_zh)}</h3><div class="title-en">{html_escape(article.get('title'))}</div><div class="translated-body"><strong>新闻正文/简要中文翻译</strong>{html_escape(translated)}</div></div>
    <div class="lang-en" hidden><h3 style="color:#9b2c2c">{html_escape(article.get('title'))}</h3><div class="original"><strong>Original Body/Excerpt</strong><br>{html_escape(original)}</div></div>
    <details><summary>查看新闻五要素</summary><dl class="five-grid">{_five_elements(analysis, fields)}</dl></details>
    <div class="links"><a href="{link}">查看原始报道</a></div>
    <div class="audit">标题翻译：{_attempt_label(audit.get('title') or {})}；正文翻译：{_attempt_label(audit.get('abstract_or_body') or {})}；分析：{html_escape((article.get('analysis') or {}).get('status') or 'unknown')}</div>
  </div>
</article>"""




def _source_health(issue: dict[str, Any]) -> str:
    status = issue.get("source_status") or {}
    rows = status.get("sources") or []
    if not rows:
        return ""
    cells = []
    for row in rows:
        failed = int(row.get("failed_queries") or 0)
        skipped = int(row.get("skipped_queries") or 0)
        ok = int(row.get("successful_queries") or 0)
        zero = int(row.get("zero_result_queries") or 0)
        health = clean_space(row.get("health"))
        state_map = {"healthy": "正常", "degraded": "部分失败", "empty": "成功但无结果", "failed": "失败", "skipped": "跳过"}
        state = state_map.get(health, "正常" if failed == 0 else ("部分失败" if ok else "失败"))
        cells.append(
            f'<tr><td>{html_escape(row.get("source"))}</td><td>{state}</td>'
            f'<td>{ok}</td><td>{zero}</td><td>{failed}</td><td>{skipped}</td>'
            f'<td>{int(row.get("records_reported") or 0)}</td></tr>'
        )
    funnel = issue.get("retrieval_funnel") or {}
    papers = funnel.get("papers") or {}
    news = funnel.get("news") or {}
    review = issue.get("relevance_review") or {}
    paper_review = review.get("papers") or {}
    news_review = review.get("news") or {}
    anchor = issue.get("anchor_coverage") or {}
    identities = anchor.get("identities") or []
    covered = 0
    unexecuted = 0
    for identity in identities:
        provider_rows = (identity.get("providers") or {}).values()
        if any(int(x.get("records_reported") or 0) > 0 for x in provider_rows):
            covered += 1
        if any(int(x.get("queries_planned") or 0) > int(x.get("queries_executed") or 0) for x in provider_rows):
            unexecuted += 1
    return (
        '<details class="source-health"><summary>查看检索源健康、身份锚点覆盖与全量相关性复核</summary>'
        f'<p>文献：原始 {papers.get("raw",0)} → 时间窗 {papers.get("after_window",0)} → '
        f'候选闸门 {papers.get("after_candidate_gate",0)} → 最终闸门 {papers.get("after_final_gate",0)} → '
        f'展示 {papers.get("displayed",0)}。</p>'
        f'<p>新闻：原始 {news.get("raw",0)} → 时间窗 {news.get("after_window",0)} → '
        f'候选闸门 {news.get("after_candidate_gate",0)} → 最终闸门 {news.get("after_final_gate",0)} → '
        f'展示 {news.get("displayed",0)}。</p>'
        f'<p>相关性复核：Python 已检查全部候选文献 {paper_review.get("candidates_reviewed_by_python",0)} 条、新闻 '
        f'{news_review.get("candidates_reviewed_by_python",0)} 条；LLM 使用证据句紧凑包按 Token 动态分批，'
        f'无篇数上限、无摘要前缀字符截断；仅 U 类记录升级为更完整证据。</p>'
        f'<p>单身份锚点：共 {anchor.get("identity_count",0)} 个；本次至少一个来源命中的锚点 {covered} 个；'
        f'存在计划但未执行来源的锚点 {unexecuted} 个。完整逐词统计保存在 data/audit/anchor_coverage.json。</p>'
        '<div style="overflow-x:auto"><table><thead><tr><th>来源</th><th>状态</th>'
        '<th>成功查询</th><th>成功但0条</th><th>失败</th><th>跳过</th><th>返回记录</th>'
        f'</tr></thead><tbody>{"".join(cells)}</tbody></table></div></details>'
    )

def _section(title: str, cls: str, cards: list[str]) -> str:
    if not cards:
        return ""
    return f'<section><h2 class="section-title {cls}">{html_escape(title)}</h2>{"".join(cards)}</section>'


def render_site(issue: dict[str, Any], output_dir: Path) -> None:
    site_dir = output_dir / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    papers = issue.get("papers") or []
    research = [p for p in papers if p.get("paper_type") == "research"]
    reviews = [p for p in papers if p.get("paper_type") == "review"]
    news = issue.get("news") or []
    overview = issue.get("overview") or {}
    paper_tiers = {tier: sum(1 for x in papers if x.get("priority_tier") == tier) for tier in ("A", "B", "C")}
    news_tiers = {tier: sum(1 for x in news if x.get("priority_tier") == tier) for tier in ("A", "B", "C")}
    tier_summary = (
        f"文献优先级 A/B/C：{paper_tiers['A']}/{paper_tiers['B']}/{paper_tiers['C']}；"
        f"新闻优先级 A/B/C：{news_tiers['A']}/{news_tiers['B']}/{news_tiers['C']}。"
    )
    sections = [
        _section("📘 研究型文献 / Research Articles", "research", [paper_card(x) for x in research]),
        _section("📗 综述与观点 / Reviews", "review", [paper_card(x) for x in reviews]),
        _section("🚨 突发动态与新闻 / Health News", "news", [news_card(x) for x in news]),
    ]
    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html_escape(issue['title_zh'])}</title><style>{SITE_CSS}</style></head><body><main class="page">
<header class="hero"><img src="assets/cover.jpg" alt="{html_escape(issue['title_zh'])}"><div class="hero-text"><h1>{html_escape(issue['title_zh'])}</h1><p>{html_escape(issue['issue_date'])} | 深度科研 + 全球新闻动态 | {html_escape(issue['window_start'])}—{html_escape(issue['window_end'])}</p></div></header>
<section class="overview"><h2>💡 今日核心综述 / Summary</h2><p class="lang-zh">{html_escape(overview.get('zh'))}</p><p class="lang-en" hidden>{html_escape(overview.get('en'))}</p><p style="font-size:12px;color:#718096;">{html_escape(tier_summary)}</p></section>
<div class="toolbar"><button class="language-toggle" data-language="zh">zh</button><button class="language-toggle" data-language="en">en</button></div>
<div class="stats"><div><strong>{len(research)}</strong><span>研究文献</span></div><div><strong>{len(reviews)}</strong><span>综述文献</span></div><div><strong>{len(news)}</strong><span>新闻报道</span></div><div><strong>{issue.get('metrics',{}).get('translated',0)}</strong><span>中文翻译</span></div></div>
<div class="content">{_source_health(issue)}{''.join(sections)}</div>
<footer>数据来源包括 PubMed、Europe PMC、Crossref、Semantic Scholar、OpenAlex、bioRxiv/medRxiv 与公开新闻/卫生机构网页。无摘要或正文时不生成研究发现或新闻结论；五要素解读受现有证据约束。</footer>
</main><script>{SITE_JS}</script></body></html>"""
    (site_dir / "index.html").write_text(html, encoding="utf-8")

    feed_items = []
    for item in (papers[:10] + news[:10]):
        title = item.get("title_zh") or item.get("title")
        link = f"https://doi.org/{item.get('doi')}" if item.get("doi") else item.get("resolved_url") or item.get("url") or ""
        description = item.get("abstract_zh") or item.get("content_zh") or item.get("summary_zh") or ""
        feed_items.append(f"<item><title>{html_escape(title)}</title><link>{html_escape(link)}</link><description>{html_escape(description)}</description></item>")
    feed = f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{html_escape(issue["title_zh"])}</title><link>./</link><description>{html_escape(issue["title_en"])}</description>{"".join(feed_items)}</channel></rss>'
    (site_dir / "feed.xml").write_text(feed, encoding="utf-8")


def render_wechat_package(issue: dict[str, Any], output_dir: Path, cover_meta: dict[str, Any]) -> None:
    package = output_dir / "wechat-package"
    package.mkdir(parents=True, exist_ok=True)
    papers = issue.get("papers") or []
    research = [p for p in papers if p.get("paper_type") == "research"]
    reviews = [p for p in papers if p.get("paper_type") == "review"]
    news = issue.get("news") or []
    overview = issue.get("overview") or {}
    body = f"""
<section style="font-family:Arial,'Noto Sans CJK SC',sans-serif;color:#333;line-height:1.75;">
  <section style="padding:22px;background:{COLORS['navy']};color:#fff;text-align:center;">
    <h1 style="margin:0;font-size:24px;">{html_escape(issue['title_zh'])}</h1>
    <p style="margin:8px 0 0;font-size:13px;opacity:.85;">{html_escape(issue['issue_date'])} | 深度科研 + 全球新闻动态</p>
  </section>
  <section style="padding:18px;background:{COLORS['amber_bg']};border-bottom:5px solid {COLORS['amber_line']};">
    <h2 style="margin:0 0 8px;color:{COLORS['amber']};font-size:18px;">💡 今日核心综述</h2>
    <p style="margin:0;font-size:15px;line-height:1.75;">{html_escape(overview.get('zh'))}</p>
  </section>
  <h2 style="border-left:6px solid {COLORS['paper_green']};padding-left:12px;color:{COLORS['paper_green']};font-size:20px;">📘 学术前沿</h2>
  {''.join(paper_card(x, wechat=True) for x in research + reviews) or '<p>本期无符合证据要求的学术文献。</p>'}
  <h2 style="border-left:6px solid {COLORS['news_red']};padding-left:12px;color:{COLORS['news_red']};font-size:20px;">🚨 突发动态与新闻</h2>
  {''.join(news_card(x, wechat=True) for x in news) or '<p>本期无抓获正文且通过相关性审核的新闻。</p>'}
  <p style="padding:16px;background:{COLORS['navy']};color:#fff;text-align:center;font-size:11px;">监测引擎：多源学术数据库与公开卫生信息源；翻译：Gemini/Groq/Google/MyMemory 多级回退；解读受原始摘要或正文约束。</p>
</section>"""
    article_file = package / "article.html"
    article_file.write_text(body, encoding="utf-8")
    manifest = {
        "schema_version": 2,
        "contract": "pathogen-wechat-package/v2",
        "publish_key": issue["issue_id"],
        "profile_id": issue["profile_id"],
        "report_date": issue["issue_date"],
        "title": f"{issue['title_zh']}｜{issue['issue_date']}",
        "digest": (overview.get("zh") or "")[:120],
        "content_file": "article.html",
        "content_source_url": os.getenv("PIF_CONTENT_SOURCE_URL", "").strip(),
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
        },
    }
    dump_json(package / "manifest.json", manifest)
