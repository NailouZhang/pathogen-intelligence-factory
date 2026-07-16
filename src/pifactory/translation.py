from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import requests
from deep_translator import GoogleTranslator, MyMemoryTranslator

from .llm import LLMError, LLMRouter
from .utils import clean_space, extract_numbers, sha256_text, split_sentences, truncate


TRANSLATION_CACHE_VERSION = "v2.1-abstract-google-fallback"

DEFAULT_REPAIRS = {
    "汉塔病毒": "汉坦病毒",
    "韩坦病毒": "汉坦病毒",
    "宋病毒": "汉坦病毒",
    "汉坦病毒es": "汉坦病毒",
    "汉坦病毒s": "汉坦病毒",
    "正汉坦病毒es": "正汉坦病毒",
    "正汉坦病毒s": "正汉坦病毒",
    "安第斯汉坦病毒": "安第斯病毒",
    "安第斯 hantavirus": "安第斯病毒",
    "汉坦病毒肆虐的": "发生汉坦病毒疫情的",
    "汉坦病毒肆虐": "发生汉坦病毒疫情",
    "明显的汉坦病毒疫情": "疑似汉坦病毒疫情",
    "明显汉坦病毒疫情": "疑似汉坦病毒疫情",
}

FORBIDDEN_TRANSLATIONS = (
    "宋病毒",
    "汉塔病毒",
    "韩坦病毒",
    "汉坦病毒es",
    "正汉坦病毒es",
)

GOOGLE_DIRECT_URL = "https://translate.googleapis.com/translate_a/single"


def _glossary(profile: dict[str, Any]) -> list[dict[str, str]]:
    rows = profile.get("translation_glossary") or []
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        source = clean_space(row.get("source"))
        target = clean_space(row.get("target"))
        if not source or not target:
            continue
        pair = (source.casefold(), target)
        if pair in seen:
            continue
        seen.add(pair)
        out.append({"source": source, "target": target})
    return out


def _alpha_code(index: int) -> str:
    # Letter-only placeholders survive translation more reliably and do not pollute
    # numeric-preservation checks.
    chars: list[str] = []
    value = index
    while True:
        chars.append(chr(ord("A") + value % 26))
        value = value // 26 - 1
        if value < 0:
            break
    return "".join(reversed(chars))


def _term_pattern(source: str) -> re.Pattern[str]:
    escaped = re.escape(source)
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9 ._\-/]+", source):
        # Match the whole scientific term and a simple English plural. This prevents
        # Orthohantavirus inside Orthohantaviruses from leaving an "es" suffix.
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?:es|s)?(?![A-Za-z0-9])", flags=re.I)
    return re.compile(escaped, flags=re.I)


def _protect(text: str, glossary: list[dict[str, str]]) -> tuple[str, dict[str, str]]:
    protected = text
    mapping: dict[str, str] = {}
    rows = sorted(glossary, key=lambda x: len(x["source"]), reverse=True)
    for index, row in enumerate(rows):
        pattern = _term_pattern(row["source"])
        if not pattern.search(protected):
            continue
        token = f"ZXQTERM{_alpha_code(index)}QXZ"
        protected = pattern.sub(token, protected)
        mapping[token] = row["target"]
    return protected, mapping


def _restore(text: str, mapping: dict[str, str]) -> str:
    value = text
    for token, target in mapping.items():
        # Some translators insert spaces around long uppercase placeholders.
        compact_pattern = re.compile(r"\s*".join(re.escape(ch) for ch in token), flags=re.I)
        value = compact_pattern.sub(target, value)
        value = re.sub(re.escape(token), target, value, flags=re.I)
    return value


def _repair_zh(text: str, glossary: list[dict[str, str]]) -> str:
    value = clean_space(text)
    for wrong, correct in DEFAULT_REPAIRS.items():
        value = value.replace(wrong, correct)

    # Apply exact scientific terminology after translation. Whole-term matching and
    # plural handling avoid outputs such as "正汉坦病毒es".
    for row in sorted(glossary, key=lambda x: len(x["source"]), reverse=True):
        value = _term_pattern(row["source"]).sub(row["target"], value)

    value = re.sub(r"(?<=汉坦病毒)\s+(?=[，。；：、)）])", "", value)
    value = re.sub(r"\s+(汉坦病毒|正汉坦病毒|汉坦病毒科|安第斯病毒|汉滩病毒|首尔病毒|普马拉病毒)\s+", r"\1", value)
    value = value.replace(" ,", "，").replace(",", "，")
    value = value.replace(" ;", "；").replace(";", "；")
    value = value.replace(" :", "：")
    value = re.sub(r"\s+([。！？；，：])", r"\1", value)
    value = re.sub(r"([。！？；，：])\s+", r"\1", value)
    value = re.sub(r"\s{2,}", " ", value)
    return clean_space(value)


def _normalised_numbers(text: str) -> set[str]:
    return {number.replace(",", "").replace(" ", "") for number in extract_numbers(text)}


def _looks_chinese(text: str, source: str = "", field_kind: str = "body") -> tuple[bool, str]:
    value = clean_space(text)
    if not value:
        return False, "empty"
    if any(bad in value for bad in FORBIDDEN_TRANSLATIONS):
        return False, "forbidden_pathogen_translation"
    if "ZXQTERM" in value.upper() or "PDITERM" in value.upper():
        return False, "unrestored_placeholder"
    if re.search(r"(.)\1{5,}", value):
        return False, "repeated_character_gibberish"

    chinese = len(re.findall(r"[\u4e00-\u9fff]", value))
    letters = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", value))
    minimum = 2 if field_kind in {"title", "analysis"} else 6
    ratio_floor = 0.08 if field_kind in {"title", "analysis"} else 0.12
    if chinese < minimum:
        return False, "insufficient_chinese"
    if letters and chinese / letters < ratio_floor:
        return False, "excessive_untranslated_english"

    src_numbers = _normalised_numbers(source)
    dst_numbers = _normalised_numbers(value)
    if src_numbers and not src_numbers.issubset(dst_numbers):
        return False, "number_loss"
    return True, "ok"


def _split_translation_chunks(text: str, limit: int = 2600) -> list[str]:
    value = clean_space(text)
    if not value:
        return []
    if len(value) <= limit:
        return [value]
    sentences = split_sentences(value, max_sentences=500)
    if not sentences:
        return [value[i : i + limit] for i in range(0, len(value), limit)]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(sentence[i : i + limit] for i in range(0, len(sentence), limit))
            continue
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > limit:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _retry(call: Callable[[], str], attempts: int = 3) -> str:
    last_error: Exception | None = None
    for index in range(attempts):
        try:
            result = clean_space(call())
            if result:
                return result
        except Exception as exc:  # noqa: PERF203 - retry boundary is intentional
            last_error = exc
        if index + 1 < attempts:
            time.sleep(0.8 * (2**index))
    raise RuntimeError(str(last_error or "translation returned empty text"))


def _google_deep_chunk(text: str) -> str:
    # Deep-translator is retained because it already worked in the user's Actions
    # run. Both target variants are attempted because provider language aliases can
    # differ between releases.
    errors: list[str] = []
    for target in ("zh-CN", "zh"):
        try:
            return _retry(lambda: GoogleTranslator(source="en", target=target).translate(text), attempts=3)
        except Exception as exc:
            errors.append(f"{target}: {exc}")
    raise RuntimeError("; ".join(errors))


def _google_direct_chunk(text: str) -> str:
    def call() -> str:
        response = requests.get(
            GOOGLE_DIRECT_URL,
            params={"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text},
            headers={
                "User-Agent": os.getenv(
                    "PIF_USER_AGENT",
                    "Mozilla/5.0 PathogenIntelligenceFactory/1.0",
                )
            },
            timeout=35,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload[0] if isinstance(payload, list) and payload else []
        translated = "".join(str(row[0]) for row in rows if isinstance(row, list) and row and row[0])
        if not translated:
            raise RuntimeError("Google direct endpoint returned no translated segments")
        return translated

    return _retry(call, attempts=3)


def _mymemory_chunk(text: str) -> str:
    errors: list[str] = []
    for target in ("zh-CN", "zh"):
        try:
            return _retry(lambda: MyMemoryTranslator(source="en", target=target).translate(text), attempts=2)
        except Exception as exc:
            errors.append(f"{target}: {exc}")
    raise RuntimeError("; ".join(errors))


def _python_translate(text: str) -> tuple[str, str, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    chunks = _split_translation_chunks(text)
    providers: list[tuple[str, Callable[[str], str]]] = [
        ("python_google_translate", _google_deep_chunk),
        ("python_google_direct", _google_direct_chunk),
        ("python_mymemory", _mymemory_chunk),
    ]
    for provider, translator in providers:
        translated_chunks: list[str] = []
        try:
            for chunk in chunks:
                translated_chunks.append(translator(chunk))
            value = clean_space(" ".join(translated_chunks))
            if value:
                errors.append({"provider": provider, "status": "success", "chunks": len(chunks)})
                return value, provider, errors
        except Exception as exc:
            errors.append({"provider": provider, "status": "failed", "error": clean_space(exc)[:500]})
    raise RuntimeError(json.dumps(errors, ensure_ascii=False))


def _cache_key(source: str, glossary: list[dict[str, str]], field_kind: str) -> str:
    return sha256_text(
        TRANSLATION_CACHE_VERSION
        + "|"
        + field_kind
        + "|"
        + source
        + json.dumps(glossary, ensure_ascii=False, sort_keys=True)
    )


def translate_text(
    text: str,
    *,
    profile: dict[str, Any],
    llm: LLMRouter,
    prompt_text: str,
    cache: dict[str, Any],
    max_chars: int = 6500,
    field_kind: str = "body",
) -> tuple[str, dict[str, Any]]:
    source = truncate(text, max_chars)
    if not source:
        return "", {"status": "empty_source", "provider": "none", "attempts": []}
    if re.search(r"[\u4e00-\u9fff]", source) and len(re.findall(r"[\u4e00-\u9fff]", source)) > len(source) * 0.2:
        return source, {"status": "source_already_chinese", "provider": "source", "attempts": []}

    glossary = _glossary(profile)
    key = _cache_key(source, glossary, field_kind)
    cached = cache.get(key)
    if isinstance(cached, dict) and cached.get("text"):
        cached_text = _repair_zh(cached["text"], glossary)
        valid, _ = _looks_chinese(cached_text, source, field_kind)
        if valid:
            return cached_text, {**cached.get("audit", {}), "from_cache": True}

    attempts: list[dict[str, Any]] = []

    # Professional LLM translation remains first. A rejected or omitted field never
    # ends the chain; it always enters the independent Google/Python fallback.
    if llm.available:
        protected, mapping = _protect(source, glossary)
        prompt = json.dumps(
            {
                "source_language": "English",
                "target_language": "Simplified Chinese",
                "field_kind": field_kind,
                "text": protected,
                "protected_tokens": list(mapping),
                "glossary": glossary,
            },
            ensure_ascii=False,
        )
        try:
            result = llm.json_task(system=prompt_text, prompt=prompt, max_models_per_provider=3)
            attempts.extend(result.attempts)
            raw = ""
            if isinstance(result.data, dict):
                raw = clean_space(result.data.get("translation_zh") or result.data.get("translation"))
            candidate = _repair_zh(_restore(raw, mapping), glossary)
            valid, reason = _looks_chinese(candidate, source, field_kind)
            if valid:
                audit = {
                    "status": "passed_llm",
                    "provider": result.provider,
                    "model": result.model,
                    "attempts": attempts,
                }
                cache[key] = {"text": candidate, "audit": audit}
                return candidate, audit
            attempts.append(
                {
                    "provider": result.provider,
                    "model": result.model,
                    "status": "quality_rejected",
                    "reason": reason,
                }
            )
        except LLMError as exc:
            attempts.append({"provider": "llm_router", "status": "failed", "error": clean_space(exc)[:600]})

    # Google translation is a real, multi-route fallback. It is retried, chunked,
    # and followed by terminology repair. The direct endpoint is independent of
    # deep-translator, so one Google path failing does not end translation.
    try:
        raw, provider, python_attempts = _python_translate(source)
        attempts.extend(python_attempts)
        candidate = _repair_zh(raw, glossary)
        valid, reason = _looks_chinese(candidate, source, field_kind)
        if valid:
            audit = {"status": "passed_python_fallback", "provider": provider, "attempts": attempts}
            cache[key] = {"text": candidate, "audit": audit}
            return candidate, audit
        attempts.append({"provider": provider, "status": "quality_rejected", "reason": reason})
    except Exception as exc:
        attempts.append({"provider": "python_translation", "status": "failed", "error": clean_space(exc)[:800]})

    audit = {"status": "translation_unavailable", "provider": "none", "attempts": attempts}
    cache[key] = {"text": "", "audit": audit}
    return "", audit


def _analysis_fields(kind: str) -> tuple[list[str], list[str]]:
    if kind == "research":
        return ["background", "methods", "results", "contribution", "limitations"], ["背景", "方法", "结果", "贡献", "局限"]
    if kind == "review":
        return ["background", "main_directions", "current_state", "gaps", "future_research"], ["背景", "主要方向", "研究现状", "不足", "后续研究"]
    return ["time", "location", "event", "impact", "status"], ["时间", "地点", "事件", "影响", "状态"]


def _translate_field_map(
    source_fields: dict[str, str],
    field_kinds: dict[str, str],
    *,
    profile: dict[str, Any],
    llm: LLMRouter,
    prompt_text: str,
    cache: dict[str, Any],
) -> tuple[dict[str, str], dict[str, Any]]:
    """Translate one record in one LLM call, then fallback field by field.

    This preserves v2.0's speed while preventing one malformed/missing model field
    from invalidating the other fields. Google/Python fallback is always executed for
    every unresolved field.
    """
    glossary = _glossary(profile)
    translated: dict[str, str] = {}
    audits: dict[str, Any] = {}
    unresolved: dict[str, str] = {}
    cache_keys: dict[str, str] = {}

    for key, source_value in source_fields.items():
        source = clean_space(source_value)
        if not source:
            audits[key] = {"status": "empty_source", "provider": "none", "attempts": []}
            continue
        if re.search(r"[\u4e00-\u9fff]", source) and len(re.findall(r"[\u4e00-\u9fff]", source)) > len(source) * 0.2:
            translated[key] = source
            audits[key] = {"status": "source_already_chinese", "provider": "source", "attempts": []}
            continue
        field_kind = field_kinds.get(key, "body")
        cache_key = _cache_key(source, glossary, field_kind)
        cache_keys[key] = cache_key
        cached = cache.get(cache_key)
        if isinstance(cached, dict) and cached.get("text"):
            candidate = _repair_zh(cached["text"], glossary)
            valid, _ = _looks_chinese(candidate, source, field_kind)
            if valid:
                translated[key] = candidate
                audits[key] = {**cached.get("audit", {}), "from_cache": True}
                continue
        unresolved[key] = source

    llm_attempts: list[dict[str, Any]] = []
    if unresolved and llm.available:
        protected_fields: dict[str, str] = {}
        mappings: dict[str, dict[str, str]] = {}
        for key, source in unresolved.items():
            protected, mapping = _protect(source, glossary)
            protected_fields[key] = protected
            mappings[key] = mapping
        prompt = json.dumps(
            {
                "source_language": "English",
                "target_language": "Simplified Chinese",
                "fields": protected_fields,
                "field_kinds": {key: field_kinds.get(key, "body") for key in unresolved},
                "glossary": glossary,
                "instruction": "Translate every field faithfully. Do not omit any key and do not summarize the abstract/body.",
            },
            ensure_ascii=False,
        )
        try:
            result = llm.json_task(system=prompt_text, prompt=prompt, max_models_per_provider=3)
            llm_attempts = result.attempts
            response_fields = result.data.get("translations") if isinstance(result.data, dict) else {}
            if not isinstance(response_fields, dict):
                response_fields = {}
            for key, source in list(unresolved.items()):
                raw = clean_space(response_fields.get(key))
                candidate = _repair_zh(_restore(raw, mappings.get(key, {})), glossary)
                valid, reason = _looks_chinese(candidate, source, field_kinds.get(key, "body"))
                if valid:
                    audit = {
                        "status": "passed_llm",
                        "provider": result.provider,
                        "model": result.model,
                        "attempts": llm_attempts,
                    }
                    translated[key] = candidate
                    audits[key] = audit
                    cache[cache_keys[key]] = {"text": candidate, "audit": audit}
                    unresolved.pop(key, None)
                elif raw:
                    audits[key] = {
                        "status": "llm_quality_rejected",
                        "provider": result.provider,
                        "model": result.model,
                        "reason": reason,
                        "attempts": llm_attempts,
                    }
        except LLMError as exc:
            llm_attempts = [{"provider": "llm_router", "status": "failed", "error": clean_space(exc)[:700]}]

    # Every field omitted or rejected by the LLM receives the full Google/Python
    # fallback. This is the essential v2.1 coverage guarantee.
    for key, source in list(unresolved.items()):
        try:
            raw, provider, python_attempts = _python_translate(source)
            candidate = _repair_zh(raw, glossary)
            valid, reason = _looks_chinese(candidate, source, field_kinds.get(key, "body"))
            attempts = llm_attempts + python_attempts
            if valid:
                audit = {"status": "passed_python_fallback", "provider": provider, "attempts": attempts}
                translated[key] = candidate
                audits[key] = audit
                cache[cache_keys[key]] = {"text": candidate, "audit": audit}
                unresolved.pop(key, None)
            else:
                audits[key] = {
                    "status": "translation_unavailable",
                    "provider": provider,
                    "reason": reason,
                    "attempts": attempts + [{"provider": provider, "status": "quality_rejected", "reason": reason}],
                }
        except Exception as exc:
            audits[key] = {
                "status": "translation_unavailable",
                "provider": "none",
                "attempts": llm_attempts + [{"provider": "python_translation", "status": "failed", "error": clean_space(exc)[:800]}],
            }

    return translated, audits


def translate_record(
    record: dict[str, Any],
    *,
    profile: dict[str, Any],
    llm: LLMRouter,
    prompts_dir: Path,
    cache: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    prompt_text = (prompts_dir / "translate_zh.md").read_text(encoding="utf-8")
    analysis = (record.get("analysis") or {}).get("analysis") or {}
    fields, _labels = _analysis_fields(kind)

    title_source = truncate(clean_space(record.get("title")), 900)
    if kind in {"research", "review"}:
        body_source = truncate(clean_space(record.get("abstract")), 9000)
        body_kind = "abstract"
    else:
        body_source = truncate(clean_space(record.get("content") or record.get("excerpt")), 7000)
        body_kind = "news_body"

    source_fields: dict[str, str] = {"title": title_source}
    field_kinds: dict[str, str] = {"title": "title"}
    if body_source:
        source_fields["abstract_or_body"] = body_source
        field_kinds["abstract_or_body"] = body_kind
    for field in fields:
        source = truncate(clean_space(analysis.get(field)), 1500)
        if source:
            source_fields[field] = source
            field_kinds[field] = "analysis"

    translated, audits = _translate_field_map(
        source_fields,
        field_kinds,
        profile=profile,
        llm=llm,
        prompt_text=prompt_text,
        cache=cache,
    )

    record["title_zh"] = translated.get("title") or "中文标题翻译暂不可用"
    record["source_body_en"] = body_source

    if body_source:
        body_zh = translated.get("abstract_or_body")
        if not body_zh:
            body_zh = "摘要中文翻译暂不可用；可切换 en 查看英文摘要。" if kind in {"research", "review"} else "新闻正文中文翻译暂不可用；可切换 en 查看原文。"
        body_audit = audits.get("abstract_or_body", {})
    elif kind in {"research", "review"} and clean_space(record.get("full_text")):
        body_zh = "原始记录未提供摘要；下方五要素根据已获取的正文证据生成。"
        body_audit = {"status": "no_abstract_fulltext_available", "provider": "deterministic", "attempts": []}
    elif kind in {"research", "review"}:
        body_zh = "原始记录未提供摘要或可分析正文；当前仅展示书目信息。"
        body_audit = {"status": "no_abstract_or_fulltext", "provider": "deterministic", "attempts": []}
    else:
        body_zh = "未抓获新闻正文，当前仅提供中文标题；不根据标题扩写内容。"
        body_audit = {"status": "no_news_body", "provider": "deterministic", "attempts": []}

    analysis_zh = {field: translated.get(field) or "未报告" for field in fields}
    record["abstract_zh" if kind in {"research", "review"} else "content_zh"] = body_zh
    record["summary_zh"] = body_zh
    record["analysis_zh"] = analysis_zh
    record["translation_audit"] = {
        "title": audits.get("title", {}),
        "abstract_or_body": body_audit,
        "fields": {field: audits.get(field, {"status": "empty_source", "provider": "none", "attempts": []}) for field in fields},
    }
    return record
