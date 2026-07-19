from __future__ import annotations

import inspect
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


TRANSLATION_CACHE_VERSION = "v15.2-independent-news-state-provider-health-1"

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


TRANSLATION_HEALTH_KEY = "__translation_provider_health_v15_2__"

def _translation_health(cache: dict[str, Any]) -> dict[str, Any]:
    state = cache.setdefault(TRANSLATION_HEALTH_KEY, {"policy_version": TRANSLATION_CACHE_VERSION, "providers": {}})
    if not isinstance(state, dict):
        state = {"policy_version": TRANSLATION_CACHE_VERSION, "providers": {}}
        cache[TRANSLATION_HEALTH_KEY] = state
    state.setdefault("providers", {})
    return state

def _translation_failure_category(exc: Exception) -> tuple[str, int]:
    text = clean_space(exc).lower()
    status = getattr(getattr(exc, "response", None), "status_code", None)
    headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
    retry_after = headers.get("Retry-After") or headers.get("retry-after") or 0
    try:
        retry_seconds = max(1, int(float(retry_after)))
    except (TypeError, ValueError):
        retry_seconds = 60
    if status == 429 or "429" in text or "too many requests" in text or "rate limit" in text:
        return "rate_limited", retry_seconds
    if status in {401, 403} or any(token in text for token in ("unauthorized", "forbidden", "authentication", "invalid api key")):
        return "authentication_failed", 0
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)) or any(token in text for token in ("timeout", "timed out", "connection", "network", "dns")):
        return "network_error", 0
    return "field_failure", 0

def _provider_available(health: dict[str, Any], provider: str) -> tuple[bool, str]:
    row = (health.get("providers") or {}).get(provider) or {}
    now = time.time()
    if row.get("disabled"):
        return False, clean_space(row.get("disabled_reason")) or "disabled_for_current_profile"
    until = float(row.get("cooldown_until") or 0.0)
    if until > now:
        return False, f"cooldown:{round(until-now,1)}s"
    return True, "available"

def _mark_translation_success(health: dict[str, Any], provider: str) -> None:
    row = (health.setdefault("providers", {})).setdefault(provider, {})
    row["successes"] = int(row.get("successes") or 0) + 1
    row["consecutive_network_failures"] = 0
    row["last_status"] = "success"

def _mark_translation_failure(health: dict[str, Any], provider: str, exc: Exception) -> tuple[str, int]:
    category, retry_seconds = _translation_failure_category(exc)
    row = (health.setdefault("providers", {})).setdefault(provider, {})
    row["failures"] = int(row.get("failures") or 0) + 1
    row["last_status"] = category
    row["last_error"] = clean_space(exc)[:300]
    if category == "rate_limited":
        row["cooldown_until"] = time.time() + max(1, retry_seconds)
    elif category == "authentication_failed":
        row["disabled"] = True
        row["disabled_reason"] = category
    elif category == "network_error":
        count = int(row.get("consecutive_network_failures") or 0) + 1
        row["consecutive_network_failures"] = count
        if count >= max(1, int(os.getenv("PIF_TRANSLATION_NETWORK_FAILURE_THRESHOLD", "2"))):
            row["disabled"] = True
            row["disabled_reason"] = "repeated_network_failure"
    return category, retry_seconds



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


def _python_translate(text: str, health: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    chunks = _split_translation_chunks(text)
    providers: list[tuple[str, Callable[[str], str]]] = [
        ("python_google_translate", _google_deep_chunk),
        ("python_google_direct", _google_direct_chunk),
        ("python_mymemory", _mymemory_chunk),
    ]
    for provider, translator in providers:
        available, reason = _provider_available(health, provider)
        if not available:
            attempts.append({"provider": provider, "status": "skipped", "reason": reason})
            continue
        translated_chunks: list[str] = []
        try:
            for chunk in chunks:
                translated_chunks.append(translator(chunk))
            value = clean_space(" ".join(translated_chunks))
            if value:
                _mark_translation_success(health, provider)
                attempts.append({"provider": provider, "status": "success", "chunks": len(chunks)})
                return value, provider, attempts
        except Exception as exc:
            category, retry_seconds = _mark_translation_failure(health, provider, exc)
            attempts.append({
                "provider": provider, "status": "failed", "failure_category": category,
                "retry_after_seconds": retry_seconds or None, "error": clean_space(exc)[:500],
            })
    raise RuntimeError(json.dumps(attempts, ensure_ascii=False))


def _invoke_python_translate(text: str, health: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    """Call the internal translator while preserving older test/plugin shims."""
    try:
        parameter_count = len(inspect.signature(_python_translate).parameters)
    except (TypeError, ValueError):
        parameter_count = 2
    if parameter_count <= 1:
        return _python_translate(text)  # type: ignore[call-arg]
    return _python_translate(text, health)


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
    max_chars: int = 30000,
    field_kind: str = "body",
) -> tuple[str, dict[str, Any]]:
    source = truncate(text, max_chars) if max_chars > 0 else clean_space(text)
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
    protected, mapping = _protect(source, glossary)

    # Free Python translation providers are always tried first.  The direct
    # requests-based Google route is independent of deep-translator, and
    # MyMemory is retained as another no-key provider.
    try:
        raw, provider, python_attempts = _invoke_python_translate(protected, _translation_health(cache))
        attempts.extend(python_attempts)
        candidate = _repair_zh(_restore(raw, mapping), glossary)
        valid, reason = _looks_chinese(candidate, source, field_kind)
        if valid:
            audit = {"status": "passed_python", "provider": provider, "attempts": attempts}
            cache[key] = {"text": candidate, "audit": audit}
            return candidate, audit
        attempts.append({"provider": provider, "status": "quality_rejected", "reason": reason})
    except Exception as exc:
        attempts.append({"provider": "python_translation", "status": "failed", "error": clean_space(exc)[:800]})

    # LLM translation is the final free fallback. It is called only after all
    # Python routes have failed or failed quality validation.
    if llm.available:
        prompt = json.dumps(
            {
                "source_language": "English",
                "target_language": "Simplified Chinese",
                "field_kind": field_kind,
                "text": protected,
                "protected_tokens": list(mapping),
                "glossary": glossary,
                "instruction": "Translate completely. Preserve all numbers and return a non-empty translation_zh field.",
            },
            ensure_ascii=False,
        )
        try:
            result = llm.json_task(system=prompt_text, prompt=prompt, provider_order=getattr(llm, "provider_order", lambda purpose: ())("rescue"), max_models_per_provider=2, temperature=0.05, task_name="translation_single_rescue")
            attempts.extend(result.attempts)
            raw = ""
            if isinstance(result.data, dict):
                raw = clean_space(result.data.get("translation_zh") or result.data.get("translation"))
            candidate = _repair_zh(_restore(raw, mapping), glossary)
            valid, reason = _looks_chinese(candidate, source, field_kind)
            if valid:
                audit = {
                    "status": "passed_llm_final_fallback",
                    "provider": result.provider,
                    "model": result.model,
                    "attempts": attempts,
                }
                cache[key] = {"text": candidate, "audit": audit}
                return candidate, audit
            attempts.append({"provider": result.provider, "model": result.model, "status": "quality_rejected", "reason": reason})
        except LLMError as exc:
            attempts.append({"provider": "llm_router", "status": "failed", "error": clean_space(exc)[:700]})

    audit = {"status": "translation_unavailable", "provider": "none", "attempts": attempts}
    cache[key] = {"text": "", "audit": audit}
    return "", audit


def _analysis_fields(kind: str) -> tuple[list[str], list[str]]:
    if kind == "research":
        return [
            "research_question_and_background",
            "study_design_and_population",
            "methods",
            "main_results",
            "interpretation_and_novelty",
            "scientific_and_public_health_significance",
            "limitations_and_evidence_strength",
        ], ["问题与背景", "设计与对象", "核心方法", "主要结果", "解释与创新", "科研与公卫意义", "局限与证据强度"]
    if kind == "review":
        return [
            "scope_and_question",
            "evidence_base_and_review_method",
            "consensus_and_key_conclusions",
            "controversies_and_evidence_gaps",
            "research_and_practice_implications",
        ], ["范围与问题", "证据基础与方法", "共识与结论", "争议与缺口", "科研与实践启示"]
    return [
        "time",
        "location_and_population",
        "event",
        "scale_impact_and_risk",
        "response_status_and_uncertainty",
    ], ["时间", "地点与对象", "事件", "规模影响与风险", "应对状态与不确定性"]


def _python_translate_protected(
    source: str,
    glossary: list[dict[str, str]],
    field_kind: str,
    health: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    protected, mapping = _protect(source, glossary)
    raw, provider, attempts = _invoke_python_translate(protected, health)
    candidate = _repair_zh(_restore(raw, mapping), glossary)
    valid, reason = _looks_chinese(candidate, source, field_kind)
    if not valid:
        raise RuntimeError(json.dumps({"provider": provider, "reason": reason, "attempts": attempts}, ensure_ascii=False))
    return candidate, {"status": "passed_python", "provider": provider, "attempts": attempts}


def _translate_field_map(
    source_fields: dict[str, str],
    field_kinds: dict[str, str],
    *,
    profile: dict[str, Any],
    llm: LLMRouter,
    prompt_text: str,
    cache: dict[str, Any],
) -> tuple[dict[str, str], dict[str, Any]]:
    """Translate every field with free Python providers first and LLM last."""
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
        try:
            candidate, audit = _python_translate_protected(source, glossary, field_kind, _translation_health(cache))
            translated[key] = candidate
            audits[key] = audit
            cache[cache_key] = {"text": candidate, "audit": audit}
        except Exception as exc:
            audits[key] = {
                "status": "python_failed",
                "provider": "python_translation",
                "attempts": [{"provider": "python_translation", "status": "failed", "error": clean_space(exc)[:800]}],
            }
            unresolved[key] = source

    # A single structured LLM request resolves all fields still missing after
    # the Python chain. This is intentionally the final fallback.
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
                "instruction": "Translate every field completely. Preserve every key and every number. Never return an empty field.",
            },
            ensure_ascii=False,
        )
        try:
            result = llm.json_task(system=prompt_text, prompt=prompt, provider_order=getattr(llm, "provider_order", lambda purpose: ())("rescue"), max_models_per_provider=2, temperature=0.05, task_name="translation_batch_rescue")
            response_fields = result.data.get("translations") if isinstance(result.data, dict) else {}
            if not isinstance(response_fields, dict):
                response_fields = {}
            for key, source in list(unresolved.items()):
                raw = clean_space(response_fields.get(key))
                candidate = _repair_zh(_restore(raw, mappings.get(key, {})), glossary)
                valid, reason = _looks_chinese(candidate, source, field_kinds.get(key, "body"))
                attempts = list((audits.get(key) or {}).get("attempts") or []) + list(result.attempts)
                if valid:
                    audit = {
                        "status": "passed_llm_final_fallback",
                        "provider": result.provider,
                        "model": result.model,
                        "attempts": attempts,
                    }
                    translated[key] = candidate
                    audits[key] = audit
                    cache[cache_keys[key]] = {"text": candidate, "audit": audit}
                    unresolved.pop(key, None)
                else:
                    audits[key] = {
                        "status": "translation_unavailable",
                        "provider": result.provider,
                        "model": result.model,
                        "reason": reason,
                        "attempts": attempts,
                    }
        except LLMError as exc:
            for key in unresolved:
                attempts = list((audits.get(key) or {}).get("attempts") or [])
                attempts.append({"provider": "llm_router", "status": "failed", "error": clean_space(exc)[:700]})
                audits[key] = {"status": "translation_unavailable", "provider": "none", "attempts": attempts}

    # Structured batch output can occasionally omit one key even when the model
    # translated all other fields. Rescue every remaining field individually so
    # a single missing analysis element does not discard an otherwise valid paper.
    for key, source in list(unresolved.items()):
        try:
            candidate, rescue_audit = translate_text(
                source,
                profile=profile,
                llm=llm,
                prompt_text=prompt_text,
                cache=cache,
                max_chars=30000,
                field_kind=field_kinds.get(key, "body"),
            )
            if candidate:
                previous_attempts = list((audits.get(key) or {}).get("attempts") or [])
                rescue_audit = dict(rescue_audit)
                rescue_audit["status"] = "passed_individual_rescue"
                rescue_audit["attempts"] = previous_attempts + list(rescue_audit.get("attempts") or [])
                translated[key] = candidate
                audits[key] = rescue_audit
                unresolved.pop(key, None)
        except Exception as exc:
            attempts = list((audits.get(key) or {}).get("attempts") or [])
            attempts.append({"provider": "individual_rescue", "status": "failed", "error": clean_space(exc)[:700]})
            audits[key] = {"status": "translation_unavailable", "provider": "none", "attempts": attempts}

    for key in unresolved:
        audits.setdefault(key, {"status": "translation_unavailable", "provider": "none", "attempts": []})
    return translated, audits


def _clip_piece(text: str, limit: int) -> str:
    value = clean_space(text)
    if len(value) <= limit:
        return value
    sentences = split_sentences(value, max_sentences=30)
    output = ""
    for sentence in sentences:
        candidate = clean_space(f"{output}{sentence}")
        if len(candidate) > limit:
            break
        output = candidate
    return output or truncate(value, limit)


def build_wechat_news_summary(analysis_zh: dict[str, str], body_zh: str, limit: int = 500) -> str:
    labels = [
        ("时间", "time", 65),
        ("地点与对象", "location_and_population", 85),
        ("事件", "event", 125),
        ("影响与风险", "scale_impact_and_risk", 105),
        ("应对与不确定性", "response_status_and_uncertainty", 105),
    ]
    parts: list[str] = []
    for label, key, budget in labels:
        value = _clip_piece(analysis_zh.get(key, ""), budget)
        if value:
            parts.append(f"{label}：{value}")
    result = clean_space("；".join(parts)).replace(":", "：").replace(";", "；")
    if not result:
        result = _clip_piece(body_zh, limit)
    if len(result) > limit:
        result = _clip_piece(result, limit)
    # clean_space() applies NFKC normalization, which converts Chinese full-width
    # punctuation to ASCII. Restore Chinese punctuation only after every clipping
    # operation so the final WeChat text remains natural and predictable.
    result = result[:limit].replace(":", "：").replace(";", "；").replace(",", "，")
    return result




def translate_title_only(
    record: dict[str, Any],
    *,
    profile: dict[str, Any],
    llm: LLMRouter,
    prompts_dir: Path,
    cache: dict[str, Any],
) -> dict[str, Any]:
    """Translate a supplementary-paper title without inventing study content."""
    prompt_text = (prompts_dir / "translate_zh.md").read_text(encoding="utf-8")
    title_zh, audit = translate_text(
        clean_space(record.get("title")),
        profile=profile, llm=llm, prompt_text=prompt_text, cache=cache,
        max_chars=800, field_kind="title",
    )
    translated = clean_space(title_zh)
    fallback_used = False
    if not translated:
        translated = clean_space(record.get("title"))
        fallback_used = True
        audit = {
            **(audit or {}),
            "status": "english_title_fallback_after_all_translation_attempts",
            "provider": "source",
            "fallback_used": True,
        }
    record["title_zh"] = translated
    record["supplementary_translation_audit"] = {
        "title": audit,
        "ready": bool(record["title_zh"]),
        "fallback_used": fallback_used,
    }
    return record

def translate_record(
    record: dict[str, Any],
    *,
    profile: dict[str, Any],
    llm: LLMRouter,
    prompts_dir: Path,
    cache: dict[str, Any],
    kind: str,
    wechat_news_max_zh_chars: int = 500,
) -> dict[str, Any]:
    prompt_text = (prompts_dir / "translate_zh.md").read_text(encoding="utf-8")
    analysis_block = record.get("analysis") or {}
    analysis = analysis_block.get("analysis") or {}
    fields, _labels = _analysis_fields(kind)

    title_source = clean_space(record.get("title"))
    if kind in {"research", "review"}:
        body_source = clean_space(record.get("abstract") or record.get("full_text_excerpt"))
        body_kind = "abstract" if clean_space(record.get("abstract")) else "full_text_excerpt"
    else:
        # News cards use the body-grounded compact brief generated during the
        # single-record analysis. Full original text remains available in the
        # English details panel and is not sent through expensive translation.
        body_source = clean_space(analysis_block.get("brief_en"))
        body_kind = "news_brief"

    source_fields: dict[str, str] = {"title": title_source}
    field_kinds: dict[str, str] = {"title": "title"}
    if body_source:
        source_fields["abstract_or_body"] = body_source
        field_kinds["abstract_or_body"] = body_kind
    for field in fields:
        source = clean_space(analysis.get(field))
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

    title_zh = clean_space(translated.get("title"))
    body_zh = clean_space(translated.get("abstract_or_body"))
    if kind in {"research", "review"} and not body_source and clean_space(record.get("full_text")):
        body_zh = "原始记录未提供摘要；结构化解读依据已获取的合法开放正文证据生成。"
        audits["abstract_or_body"] = {"status": "no_abstract_open_text_available", "provider": "deterministic", "attempts": []}
    elif kind in {"research", "review"} and not body_source:
        body_zh = "原始数据库记录未提供摘要。"
        audits["abstract_or_body"] = {"status": "no_source_abstract", "provider": "deterministic", "attempts": []}

    elements_en = {field: clean_space(analysis.get(field)) for field in fields}
    analysis_zh = {field: clean_space(translated.get(field)) for field in fields}
    # Explicit parallel entities prevent renderers from depending on the nested
    # provider response shape.  English is the original LLM analysis; Chinese is
    # translated once, avoiding a second analysis call.
    record["elements_en"] = elements_en
    record["elements_zh"] = analysis_zh
    record["analysis_en"] = elements_en
    title_ready = bool(title_zh)
    body_ready = bool(body_zh)
    translated_analysis_ready = all(bool(analysis_zh.get(field)) for field in fields)
    translation_complete = title_ready and body_ready and translated_analysis_ready

    # News translation quality is independent from source and English-analysis
    # eligibility. Missing Chinese fields are filled with verified English text
    # later by news_state.finalize_news_state and are never reported as a real
    # Chinese translation success.
    record["title_zh"] = title_zh
    record["source_body_en"] = body_source
    record["abstract_zh" if kind in {"research", "review"} else "content_zh"] = body_zh
    record["summary_zh"] = body_zh
    record["analysis_zh"] = analysis_zh
    record["translation_complete"] = translation_complete
    record["translation_status"] = "complete" if translation_complete else "unavailable"
    if kind == "news":
        summary_elements = analysis_zh if translated_analysis_ready else elements_en
        summary_body = body_zh or body_source
        record["wechat_summary_zh"] = build_wechat_news_summary(summary_elements, summary_body, limit=wechat_news_max_zh_chars)
        record["wechat_summary_ready"] = bool(record["wechat_summary_zh"])
    record["translation_ready"] = translation_complete
    record["translation_audit"] = {
        "policy_version": TRANSLATION_CACHE_VERSION,
        "order": ["deep_translator_google", "google_direct_python", "mymemory", "llm_batch_fallback", "individual_field_rescue"],
        "title": audits.get("title", {}),
        "abstract_or_body": audits.get("abstract_or_body", {}),
        "body_source_kind": body_kind if body_source else "none",
        "fields": {field: audits.get(field, {"status": "empty_source", "provider": "none", "attempts": []}) for field in fields},
        "ready": translation_complete,
        "translation_status": record.get("translation_status"),
        "provider_health": _translation_health(cache),
    }
    return record
