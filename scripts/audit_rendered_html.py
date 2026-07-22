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


PLACEHOLDER_PHRASES_EN = (
    "Not reported in the supplied evidence.",
    "The supplied evidence does not clearly report",
    "The supplied evidence does not clearly state",
    "The supplied source does not clearly report",
    "The supplied source does not clearly identify",
    "The supplied source does not clearly describe",
)


def _normalized_coverage_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", html_lib.unescape(value).casefold())


def _placeholder_coverage(value: str) -> tuple[float, str]:
    """Return the largest whole-field coverage by a known absence phrase.

    A factual sentence may honestly contain a short clause such as
    ``Event date: Not reported in the supplied evidence; investigation...``.
    Substring matching incorrectly rejected those useful fields.  We only flag
    a field when a known absence phrase dominates at least 70% of the normalized
    field text; even then it is a publishable warning, not a structural failure.
    """
    normalized = _normalized_coverage_text(value)
    if not normalized:
        return 0.0, ""
    best = (0.0, "")
    for phrase in PLACEHOLDER_PHRASES_EN:
        needle = _normalized_coverage_text(phrase)
        if needle and needle in normalized:
            coverage = min(1.0, len(needle) / max(1, len(normalized)))
            if coverage > best[0]:
                best = (coverage, phrase)
    return best


class RenderAuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[dict[str, Any]] = []
        self.dd_rows: list[dict[str, Any]] = []
        self.meta_rows: list[str] = []
        self.toggle_languages: set[str] = set()
        self.paper_cards = 0
        self.supplementary_cards = 0
        self.news_cards = 0
        self.supplementary_violations: list[dict[str, Any]] = []

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key: value or "" for key, value in attrs}

    def _make_row(self, tag: str, attrs: list[tuple[str, str | None]]) -> dict[str, Any]:
        attr = self._attrs(attrs)
        classes = set(attr.get("class", "").split())
        parent = self.stack[-1] if self.stack else {}
        explicit_lang = (attr.get("lang") or attr.get("data-source-language") or "").strip().lower()
        if explicit_lang.startswith("en"):
            effective_lang = "en"
        elif explicit_lang.startswith("zh"):
            effective_lang = "zh"
        elif explicit_lang.startswith("ja"):
            effective_lang = "ja"
        elif explicit_lang.startswith("ko"):
            effective_lang = "ko"
        elif "lang-en" in classes:
            effective_lang = "en"
        elif "lang-zh" in classes:
            effective_lang = "zh"
        else:
            effective_lang = parent.get("effective_lang") or ""
        return {
            "tag": tag,
            "classes": classes,
            "lang_en": effective_lang == "en",
            "lang_zh": effective_lang == "zh",
            "effective_lang": effective_lang,
            "source_original": bool(parent.get("source_original")) or "source-original" in classes,
            "original_title_metadata": bool(parent.get("original_title_metadata"))
            or "original-title-metadata" in classes
            or attr.get("data-metadata-role") == "title",
            "supplementary_scope": bool(parent.get("supplementary_scope")) or "supplementary-card" in classes or "supplementary" in classes,
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
            if "supplementary" in classes or "supplementary-card" in classes:
                self.supplementary_cards += 1
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
                    "effective_lang": row.get("effective_lang") or "",
                    "source_original": bool(row.get("source_original")),
                }
            )
        if row.get("supplementary_scope") and not row.get("original_title_metadata") and (
            row["tag"] in {"details", "dl", "dd"}
            or "translated-body" in row["classes"]
            or "original" in row["classes"]
        ):
            self.supplementary_violations.append({
                "tag": row["tag"],
                "classes": sorted(row["classes"]),
                "excerpt": text[:300],
            })
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


def _script_counts(value: str) -> dict[str, int]:
    return {
        "latin": len(re.findall(r"[A-Za-z\u00C0-\u024F]", value)),
        "han": len(re.findall(r"[\u3400-\u4DBF\u4E00-\u9FFF]", value)),
        "hiragana": len(re.findall(r"[\u3040-\u309F]", value)),
        "katakana": len(re.findall(r"[\u30A0-\u30FF\u31F0-\u31FF]", value)),
        "hangul": len(re.findall(r"[\u1100-\u11FF\u3130-\u318F\uAC00-\uD7AF]", value)),
        "cyrillic": len(re.findall(r"[\u0400-\u04FF]", value)),
    }


def _han_ratio(value: str) -> float:
    counts = _script_counts(value)
    return counts["han"] / max(1, counts["latin"] + counts["han"])


def audit_html(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    parser = RenderAuditParser()
    parser.feed(raw)
    parser.close()
    findings: list[dict[str, Any]] = []

    public_forbidden = (
        "审查得出的结论是", "范围说明：", "范围说明:",
        "证据不足以建立目标病毒", "不生成目标病毒结论", "不生成结构化分析",
        "保留在补充目录", "仅保留为补充新闻",
    )
    visible_text = html_lib.unescape(re.sub(r"<[^>]+>", " ", raw))
    for phrase in public_forbidden:
        if phrase in visible_text:
            findings.append({
                "severity": "critical",
                "code": "backend_process_phrase_in_public_output",
                "phrase": phrase,
            })

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
            placeholder_coverage, placeholder_phrase = _placeholder_coverage(text)
            if placeholder_coverage >= 0.70:
                findings.append({
                    "severity": "warning",
                    "code": "english_placeholder",
                    "dd_index": index,
                    "coverage": round(placeholder_coverage, 4),
                    "matched_phrase": placeholder_phrase,
                    "excerpt": text[:300],
                })
            if len(text) >= 40 and not row.get("source_original"):
                counts = _script_counts(text)
                if counts["hiragana"] or counts["katakana"]:
                    findings.append({"severity": "critical", "code": "japanese_text_in_english_element", "dd_index": index, "excerpt": text[:300]})
                elif counts["hangul"]:
                    findings.append({"severity": "critical", "code": "korean_text_in_english_element", "dd_index": index, "excerpt": text[:300]})
                elif counts["cyrillic"] >= 2:
                    findings.append({"severity": "critical", "code": "cyrillic_text_in_english_element", "dd_index": index, "excerpt": text[:300]})
                elif counts["han"] >= 2 and _han_ratio(text) >= 0.08:
                    findings.append({"severity": "critical", "code": "chinese_text_in_english_element", "dd_index": index, "excerpt": text[:300]})

    for violation in parser.supplementary_violations:
        findings.append({
            "severity": "critical",
            "code": "supplementary_card_contains_deep_content",
            **violation,
        })

    for text in parser.meta_rows:
        if re.search(r"\b(?:Figshare|Zenodo|Dryad|Data Dryad)\b", text, flags=re.I):
            findings.append({"severity": "critical", "code": "repository_object_rendered_as_paper", "excerpt": text[:300]})

    if parser.paper_cards and parser.toggle_languages != {"zh", "en"}:
        findings.append({"severity": "critical", "code": "missing_bilingual_toggle", "languages": sorted(parser.toggle_languages)})

    critical = sum(row["severity"] == "critical" for row in findings)
    warnings = sum(row["severity"] == "warning" for row in findings)
    return {
        "schema_version": 6,
        "file": str(path),
        "paper_card_markers": parser.paper_cards,
        "supplementary_card_markers": parser.supplementary_cards,
        "news_card_markers": parser.news_cards,
        "structured_elements": len(parser.dd_rows),
        "language_toggles": sorted(parser.toggle_languages),
        "critical_count": critical,
        "warning_count": warnings,
        "findings": findings,
        "status": "failed" if critical else ("passed_with_warnings" if warnings else "passed"),
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
