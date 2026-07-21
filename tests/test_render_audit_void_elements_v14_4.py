from __future__ import annotations

from pathlib import Path

from scripts.audit_rendered_html import RenderAuditParser, audit_html


def test_void_element_does_not_leak_english_scope_into_next_chinese_card(tmp_path: Path) -> None:
    path = tmp_path / "two-cards.html"
    chinese = (
        "第二张卡片的中文结构化字段内容足够长，包含研究背景、研究设计、核心方法、"
        "主要结果、解释和公共卫生意义，因此超过四十个字符。"
    )
    path.write_text(
        f'''<!doctype html><html><body>
        <button data-language="zh"></button><button data-language="en"></button>
        <article class="card paper">
          <div class="lang-zh"><dl><dd>第一张中文字段内容足够长，用于确认中文作用域本身不会被当作英文。</dd></dl></div>
          <div class="lang-en"><div class="original">Original Abstract<br>English source body.</div>
            <dl><dd>This is a sufficiently long English analytical field for the first card and contains no Chinese text.</dd></dl>
          </div>
        </article>
        <article class="card paper">
          <div class="lang-zh"><dl><dd>{chinese}</dd></dl></div>
          <div class="lang-en"><dl><dd>This is another sufficiently long English analytical field for the second card.</dd></dl></div>
        </article>
        </body></html>''',
        encoding="utf-8",
    )

    result = audit_html(path)
    assert result["status"] == "passed"
    assert result["critical_count"] == 0
    assert result["paper_card_markers"] == 2
    assert result["news_card_markers"] == 0
    assert result["structured_elements"] == 4


def test_void_tags_never_remain_on_parser_stack() -> None:
    parser = RenderAuditParser()
    parser.feed(
        '<div class="lang-en">English<br><img src="x"><meta charset="utf-8">text</div>'
        '<div class="lang-zh"><dl><dd>中文字段内容足够长，用于检查语言继承。</dd></dl></div>'
    )
    parser.close()
    assert parser.stack == []
    assert parser.dd_rows == [
        {
            "text": "中文字段内容足够长，用于检查语言继承。",
            "lang_en": False,
            "lang_zh": True,
            "effective_lang": "zh",
            "source_original": False,
        }
    ]


def test_news_card_counter_counts_articles_not_news_section_wrapper(tmp_path: Path) -> None:
    path = tmp_path / "news.html"
    path.write_text(
        '''<html><body>
        <button data-language="zh"></button><button data-language="en"></button>
        <section class="section news"><h2>News</h2></section>
        <article class="card news"><div class="lang-en"><dl><dd>This is a valid English news field with enough words for audit.</dd></dl></div></article>
        </body></html>''',
        encoding="utf-8",
    )
    result = audit_html(path)
    assert result["news_card_markers"] == 1
