#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html as html_lib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


class RenderAuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[dict[str, Any]] = []
        self.dd_rows: list[dict[str, Any]] = []
        self.meta_rows: list[str] = []
        self.toggle_languages: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        classes = set(attr.get("class", "").split())
        inherited_en = any(row.get("lang_en") for row in self.stack)
        inherited_zh = any(row.get("lang_zh") for row in self.stack)
        row = {
            "tag": tag,
            "classes": classes,
            "lang_en": inherited_en or "lang-en" in classes,
            "lang_zh": inherited_zh or "lang-zh" in classes,
            "text": [],
        }
        self.stack.append(row)
        if attr.get("data-language"):
            self.toggle_languages.add(attr["data-language"])

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            return
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i]["tag"] == tag), None)
        if index is None:
            return
        row = self.stack.pop(index)
        text = re.sub(r"\s+", " ", "".join(row["text"])).strip()
        if self.stack and text:
            self.stack[-1]["text"].append(text + " ")
        if tag == "dd":
            self.dd_rows.append({"text": text, "lang_en": row["lang_en"], "lang_zh": row["lang_zh"]})
        if "meta-strip" in row["classes"] or (tag == "p" and ("Journal:" in text or "Source:" in text)):
            self.meta_rows.append(text)

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
    findings: list[dict[str, Any]] = []

    for index, row in enumerate(parser.dd_rows):
        text = html_lib.unescape(row["text"])
        if re.match(r"^\s*\{.*\}\s*[。.]?$", text, flags=re.S) or ("{'" in text and "':" in text):
            findings.append({"severity": "critical", "code": "python_dict_literal", "dd_index": index, "excerpt": text[:300]})
        if re.search(r"\b(?:Abbreviations?|Acronyms?)\s*[:：]|(?:缩写|缩略语)(?:表)?\s*[:：]", text, flags=re.I):
            findings.append({"severity": "critical", "code": "glossary_in_structured_element", "dd_index": index, "excerpt": text[:300]})
        if row["lang_en"]:
            if "Not reported in the supplied evidence" in text:
                findings.append({"severity": "critical", "code": "english_placeholder", "dd_index": index, "excerpt": text[:300]})
            if len(text) >= 40 and _chinese_ratio(text) >= 0.35:
                findings.append({"severity": "critical", "code": "chinese_text_in_english_element", "dd_index": index, "excerpt": text[:300]})

    for text in parser.meta_rows:
        if re.search(r"\b(?:Figshare|Zenodo|Dryad|Data Dryad)\b", text, flags=re.I):
            findings.append({"severity": "critical", "code": "repository_object_rendered_as_paper", "excerpt": text[:300]})

    paper_cards = len(re.findall(r'class=["\'][^"\']*\bpaper\b', raw, flags=re.I))
    news_cards = len(re.findall(r'class=["\'][^"\']*\bnews\b', raw, flags=re.I))
    if paper_cards and parser.toggle_languages != {"zh", "en"}:
        findings.append({"severity": "critical", "code": "missing_bilingual_toggle", "languages": sorted(parser.toggle_languages)})

    critical = sum(row["severity"] == "critical" for row in findings)
    return {
        "schema_version": 1,
        "file": str(path),
        "paper_card_markers": paper_cards,
        "news_card_markers": news_cards,
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
