#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html as html_lib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


# HTML void elements never receive an end tag. Treating them as normal stack
# entries leaks the surrounding language scope into later cards. In particular,
# an English ``<div>Original<br>Abstract</div>`` previously left ``br`` on the
# parser stack, so every later Chinese ``<dd>`` inherited ``lang-en`` and the
# quality gate produced hundreds of false positives.
VOID_ELEMENTS = {
    "area",
    "base",
    "basefont",
    "bgsound",
    "br",
    "col",
    "command",
    "embed",
    "hr",
    "img",
    "input",
    "keygen",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class RenderAuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[dict[str, Any]] = []
        self.dd_rows: list[dict[str, Any]] = []
        self.meta_rows: list[str] = []
        self.toggle_languages: set[str] = set()
        self.paper_cards = 0
        self.news_cards = 0

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key: value or "" for key, value in attrs}

    def _make_row(self, tag: str, attrs: list[tuple[str, str | None]]) -> dict[str, Any]:
        attr = self._attrs(attrs)
        classes = set(attr.get("class", "").split())
        parent = self.stack[-1] if self.stack else {}
        return {
            "tag": tag,
            "classes": classes,
            "lang_en": bool(parent.get("lang_en")) or "lang-en" in classes,
            "lang_zh": bool(parent.get("lang_zh")) or "lang-zh" in classes,
            "text": [],
        }

    def _observe_attributes(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = self._attrs(attrs)
        classes = set(attr.get("class", "").split())
        language = attr.get("data-language")
        if language:
            self.toggle_languages.add(language)
        if tag == "article" and "card" in classes:
            if "paper" in classes:
                self.paper_cards += 1
            if "news" in classes:
                self.news_cards += 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self._observe_attributes(tag, attrs)
        if tag in VOID_ELEMENTS:
            return
        self.stack.append(self._make_row(tag, attrs))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self._observe_attributes(tag, attrs)
        if tag in VOID_ELEMENTS:
            return
        self.stack.append(self._make_row(tag, attrs))
        self.handle_endtag(tag)

    def _finalize_row(self, row: dict[str, Any]) -> str:
        text = re.sub(r"\s+", " ", "".join(row["text"])).strip()
        if row["tag"] == "dd":
            self.dd_rows.append(
                {
                    "text": text,
                    "lang_en": bool(row["lang_en"]),
                    "lang_zh": bool(row["lang_zh"]),
                }
            )
        if "meta-strip" in row["classes"] or (
            row["tag"] == "p" and ("Journal:" in text or "Source:" in text)
        ):
            self.meta_rows.append(text)
        return text

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in VOID_ELEMENTS or not self.stack:
            return
        # Pop to the requested tag instead of deleting one middle entry. This
        # keeps the stack structurally valid even if upstream HTML is imperfect.
        while self.stack:
            row = self.stack.pop()
            text = self._finalize_row(row)
            if self.stack and text:
                self.stack[-1]["text"].append(text + " ")
            if row["tag"] == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.stack:
            self.stack[-1]["text"].append(data)


def _chinese_ratio(value: str) -> float:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", value))
    letters = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", value))
    return chinese / letters if letters else 0.0


def audit_html(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    parser = RenderAuditParser()
    parser.feed(raw)
    parser.close()
    findings: list[dict[str, Any]] = []

    for index, row in enumerate(parser.dd_rows):
        text = html_lib.unescape(row["text"])
        if row["lang_en"] and row["lang_zh"]:
            findings.append(
                {
                    "severity": "critical",
                    "code": "ambiguous_language_scope",
                    "dd_index": index,
                    "excerpt": text[:300],
                }
            )
        if re.match(r"^\s*\{.*\}\s*[。.]?$", text, flags=re.S) or ("{'" in text and "':" in text):
            findings.append({"severity": "critical", "code": "python_dict_literal", "dd_index": index, "excerpt": text[:300]})
        if re.search(r"\b(?:Abbreviations?|Acronyms?)\s*[:：]|(?:缩写|缩略语)(?:表)?\s*[:：]", text, flags=re.I):
            findings.append({"severity": "critical", "code": "glossary_in_structured_element", "dd_index": index, "excerpt": text[:300]})
        if row["lang_en"] and not row["lang_zh"]:
            if "Not reported in the supplied evidence" in text:
                findings.append({"severity": "critical", "code": "english_placeholder", "dd_index": index, "excerpt": text[:300]})
            if len(text) >= 40 and _chinese_ratio(text) >= 0.35:
                findings.append({"severity": "critical", "code": "chinese_text_in_english_element", "dd_index": index, "excerpt": text[:300]})

    for text in parser.meta_rows:
        if re.search(r"\b(?:Figshare|Zenodo|Dryad|Data Dryad)\b", text, flags=re.I):
            findings.append({"severity": "critical", "code": "repository_object_rendered_as_paper", "excerpt": text[:300]})

    if parser.paper_cards and parser.toggle_languages != {"zh", "en"}:
        findings.append({"severity": "critical", "code": "missing_bilingual_toggle", "languages": sorted(parser.toggle_languages)})

    critical = sum(row["severity"] == "critical" for row in findings)
    return {
        "schema_version": 2,
        "file": str(path),
        "paper_card_markers": parser.paper_cards,
        "news_card_markers": parser.news_cards,
        "structured_elements": len(parser.dd_rows),
        "language_toggles": sorted(parser.toggle_languages),
        "critical_count": critical,
        "findings": findings,
        "status": "failed" if critical else "passed",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("html_file")
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()
    result = audit_html(Path(args.html_file).expanduser().resolve())
    output = json.dumps(result, ensure_ascii=False, indent=2)
    print(output)
    if args.json_out:
        target = Path(args.json_out).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output + "\n", encoding="utf-8")
    return 2 if result["critical_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
