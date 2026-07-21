from __future__ import annotations

import re
from typing import Any

from .utils import clean_space

_LATIN_RE = re.compile(r"[A-Za-z\u00C0-\u024F]")
_HAN_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF]")
_HIRAGANA_RE = re.compile(r"[\u3040-\u309F]")
_KATAKANA_RE = re.compile(r"[\u30A0-\u30FF\u31F0-\u31FF]")
_HANGUL_RE = re.compile(r"[\u1100-\u11FF\u3130-\u318F\uAC00-\uD7AF]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")

_LANGUAGE_ALIASES = {
    "eng": "en", "english": "en", "en-us": "en", "en-gb": "en",
    "jpn": "ja", "japanese": "ja", "jp": "ja",
    "zho": "zh", "chi": "zh", "chinese": "zh", "zh-cn": "zh", "zh-tw": "zh",
    "kor": "ko", "korean": "ko",
}

_LANGUAGE_LABELS = {
    "en": "English",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "ru": "Cyrillic-language",
    "und": "source-language",
}

PAPER_DEFAULTS = {
    "research_question_and_background": "The supplied evidence does not clearly state the research question or its rationale in verified English.",
    "study_design_and_population": "The supplied evidence does not clearly state the study design, setting, or study population in verified English.",
    "methods": "The supplied evidence does not clearly state the core experimental, analytical, or statistical methods in verified English.",
    "main_results": "The supplied evidence does not clearly state a directly observed main result in verified English.",
    "interpretation_and_novelty": "The supplied evidence does not clearly distinguish the authors' interpretation or the study's novelty in verified English.",
    "scientific_and_public_health_significance": "The supplied evidence does not clearly state a specific scientific or public-health implication in verified English.",
    "limitations_and_evidence_strength": "The available source-language evidence could not support a complete verified English appraisal; confidence is low.",
    "scope_and_question": "The supplied evidence does not clearly state the review scope or central question in verified English.",
    "evidence_base_and_review_method": "The supplied evidence does not clearly state the review method or evidence base in verified English.",
    "consensus_and_key_conclusions": "The supplied evidence does not clearly state a stable consensus or central conclusion in verified English.",
    "controversies_and_evidence_gaps": "The supplied evidence does not clearly state controversies or evidence gaps in verified English.",
    "research_and_practice_implications": "The supplied evidence does not clearly state a specific research or practice implication in verified English.",
}

NEWS_DEFAULTS = {
    "time": "The supplied evidence does not clearly report the event time in verified English.",
    "location_and_population": "The supplied evidence does not clearly report the location or affected population in verified English.",
    "event": "The supplied evidence does not clearly report the central event in verified English.",
    "scale_impact_and_risk": "The supplied evidence does not clearly report the scale, impact, or risk in verified English.",
    "response_status_and_uncertainty": "The supplied evidence does not clearly report the response, status, or remaining uncertainty in verified English.",
}


def normalize_language(value: Any) -> str:
    text = clean_space(value).casefold().replace("_", "-")
    if not text:
        return ""
    return _LANGUAGE_ALIASES.get(text, text.split("-", 1)[0])


def script_profile(value: Any) -> dict[str, int]:
    text = clean_space(value)
    return {
        "latin": len(_LATIN_RE.findall(text)),
        "han": len(_HAN_RE.findall(text)),
        "hiragana": len(_HIRAGANA_RE.findall(text)),
        "katakana": len(_KATAKANA_RE.findall(text)),
        "hangul": len(_HANGUL_RE.findall(text)),
        "cyrillic": len(_CYRILLIC_RE.findall(text)),
    }


def detect_text_language(value: Any, hint: Any = "") -> str:
    explicit = normalize_language(hint)
    counts = script_profile(value)
    # Strong script evidence overrides unreliable source metadata. This is
    # essential for feeds that label Japanese article records as English.
    if counts["hiragana"] or counts["katakana"]:
        return "ja"
    if counts["hangul"]:
        return "ko"
    if counts["cyrillic"] > max(2, counts["latin"]):
        return "ru"
    if counts["han"] >= 2 and counts["latin"] < counts["han"] * 2:
        return "zh" if explicit != "ja" else "ja"
    if explicit in {"en", "ja", "zh", "ko", "ru"}:
        return explicit
    if counts["latin"] >= 3:
        return "en"
    return "und"


def language_label(code: Any) -> str:
    return _LANGUAGE_LABELS.get(normalize_language(code) or "und", "source-language")


def is_verified_english(value: Any) -> bool:
    text = clean_space(value)
    if not text:
        return True
    counts = script_profile(text)
    non_latin = counts["han"] + counts["hiragana"] + counts["katakana"] + counts["hangul"] + counts["cyrillic"]
    if counts["hiragana"] or counts["katakana"] or counts["hangul"]:
        return False
    if counts["cyrillic"] >= 2:
        return False
    if counts["han"] >= 2 and counts["han"] / max(1, counts["latin"] + counts["han"]) >= 0.08:
        return False
    return bool(counts["latin"] >= 3 or non_latin == 0)


def annotate_source_language(record: dict[str, Any], *, kind: str) -> str:
    title = clean_space(record.get("title"))
    if kind == "paper":
        body = clean_space(record.get("abstract") or record.get("full_text_excerpt") or record.get("full_text"))
        record.setdefault("abstract_original", body)
    else:
        body = clean_space(record.get("content") or record.get("excerpt"))
        record.setdefault("content_original", body)
    record.setdefault("title_original", title)
    hint = record.get("source_language") or record.get("language") or record.get("lang")
    language = detect_text_language(f"{title} {body}", hint)
    record["source_language"] = language
    return language


def sanitize_english_analysis(
    data: dict[str, Any],
    *,
    kind: str,
    source_language: str = "",
) -> dict[str, Any]:
    if not isinstance(data, dict):
        return data
    fields = data.get("analysis") or {}
    defaults = NEWS_DEFAULTS if kind == "news" else PAPER_DEFAULTS
    original: dict[str, str] = {}
    replacements: list[dict[str, str]] = []
    cleaned_fields: dict[str, str] = {}
    for key, value in fields.items():
        text = clean_space(value)
        if text and not is_verified_english(text):
            original[key] = text
            replacement = defaults.get(key, "The supplied source-language evidence could not be converted into verified English.")
            cleaned_fields[key] = replacement
            replacements.append({"field": key, "detected_language": detect_text_language(text, source_language)})
        else:
            cleaned_fields[key] = text
    data["analysis"] = cleaned_fields

    summary_key = "brief_en" if kind == "news" else "summary_en"
    summary = clean_space(data.get(summary_key))
    if summary and not is_verified_english(summary):
        original[summary_key] = summary
        summary = clean_space(" ".join(x for x in cleaned_fields.values() if x))
        data[summary_key] = summary or "Verified English analysis was unavailable for the supplied source-language evidence."
        replacements.append({"field": summary_key, "detected_language": detect_text_language(original[summary_key], source_language)})
    if kind == "news":
        data["summary_en"] = clean_space(data.get("brief_en"))
    elif not clean_space(data.get("summary_en")):
        data["summary_en"] = clean_space(" ".join(x for x in cleaned_fields.values() if x))

    if original:
        data["source_language_evidence"] = original
    data["language_contract"] = {
        "policy_version": "v17.1-source-language-safe-english-1",
        "source_language": normalize_language(source_language) or "und",
        "verified_english": not replacements,
        "replacements": replacements,
        "original_evidence_preserved": bool(original),
    }
    return data
