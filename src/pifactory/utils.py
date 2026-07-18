from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8", errors="ignore")).hexdigest()


def load_json(path: str | Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def dump_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: str | Path, record: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def clean_space(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_tags(value: Any) -> str:
    # Unescape first so encoded markup such as ``&lt;b&gt;`` is also removed.
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return clean_space(text)


def normalize_title(value: Any) -> str:
    text = clean_space(value).lower()
    text = re.sub(r"\b(the|a|an)\b", " ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def unique_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = clean_space(value)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def parse_iso_date(value: Any) -> date | None:
    text = clean_space(value)
    if not text:
        return None
    match = re.search(r"(19|20)\d{2}(?:[-/]\d{1,2})?(?:[-/]\d{1,2})?", text)
    if not match:
        return None
    parts = re.split(r"[-/]", match.group(0))
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        return date(year, month, day)
    except ValueError:
        return None


def safe_date_string(value: Any) -> str | None:
    parsed = parse_iso_date(value)
    return parsed.isoformat() if parsed else None


def truncate(value: Any, limit: int) -> str:
    text = clean_space(value)
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def extract_doi(value: Any) -> str | None:
    text = str(value or "")
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, flags=re.I)
    if not match:
        return None
    return match.group(0).rstrip(".,;)]}").lower()


def extract_numbers(value: Any) -> list[str]:
    text = clean_space(value).replace(",", "")
    text = re.sub(r"(?<=\d)\s+(?=\d{3}(?:\D|$))", "", text)
    return re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?%?", text)


GLOSSARY_HEADING_RE = re.compile(
    r"(?:^|[.!?。！？]\s+|[\r\n]+)\s*(?:abbreviations?|list\s+of\s+abbreviations|acronyms?|"
    r"缩写(?:词)?(?:表)?|缩略语(?:表)?)\s*[:：]",
    flags=re.I,
)


def strip_terminal_glossary(value: Any) -> str:
    """Remove terminal abbreviation/glossary blocks before sentence selection.

    Biomedical abstracts frequently append an ``Abbreviations: A: ...; B: ...``
    section.  Those semicolon-delimited definitions are metadata, not narrative
    evidence, and must never enter methods/results/limitations extraction.
    """
    raw = html.unescape(str(value or ""))
    match = GLOSSARY_HEADING_RE.search(raw)
    if match:
        raw = raw[: match.start()]
    return clean_space(raw)


def _looks_like_glossary_fragment(text: str) -> bool:
    value = clean_space(text)
    if not value:
        return True
    colon_pairs = re.findall(r"(?:^|[;；])\s*[A-Za-z0-9][A-Za-z0-9+./_-]{1,24}\s*[:：]", value)
    return len(colon_pairs) >= 2


def split_sentences(value: Any, max_sentences: int = 50) -> list[str]:
    text = strip_terminal_glossary(value)
    if not text:
        return []
    # Semicolons are soft punctuation in scientific prose.  Splitting on them
    # created artificial "sentences" from abbreviation tables and lists.
    parts = re.split(r"(?<=[.!?。！？])\s+|[\r\n]+", text)
    output: list[str] = []
    for part in parts:
        sentence = clean_space(part)
        if len(sentence) < 20 or _looks_like_glossary_fragment(sentence):
            continue
        output.append(sentence)
        if len(output) >= max_sentences:
            break
    return output


def clean_scholarly_abstract(value: Any, *, min_duplicate_chars: int = 70) -> str:
    """Return narrative abstract text without markup, terminal glossaries or repeats.

    Metadata providers sometimes concatenate the abstract and an ``Importance``
    block twice.  Long duplicate sentences waste translation/LLM tokens and make
    the rendered card look corrupt.  Short repeated scientific phrases are kept;
    only normalized long sentence duplicates are removed.
    """
    text = strip_terminal_glossary(strip_tags(value))
    if not text:
        return ""
    rows = split_sentences(text, max_sentences=240)
    if not rows:
        return text
    seen: set[str] = set()
    kept: list[str] = []
    for row in rows:
        key_source = re.sub(
            r"^(?:abstract|background|objective|methods?|results?|findings?|conclusions?|importance)\s*[:：]?\s*",
            "", row.casefold(), flags=re.I,
        )
        key = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", key_source).strip()
        if len(row) >= min_duplicate_chars and key in seen:
            continue
        if len(row) >= min_duplicate_chars:
            seen.add(key)
        kept.append(row)
    return clean_space(" ".join(kept))


def html_escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)
