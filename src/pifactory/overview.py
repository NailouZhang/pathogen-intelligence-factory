from __future__ import annotations

import json
from typing import Any

from .llm import LLMError, LLMRouter
from .utils import clean_space


def build_overview(
    profile: dict[str, Any],
    papers: list[dict[str, Any]],
    news: list[dict[str, Any]],
    llm: LLMRouter,
) -> dict[str, str]:
    display_zh = profile.get("display_name_zh") or profile.get("profile_id")
    display_en = profile.get("display_name_en") or profile.get("profile_id")
    records = []
    for item in papers[:10]:
        records.append(
            {
                "kind": item.get("paper_type") or "research",
                "title": item.get("title"),
                "title_zh": item.get("title_zh"),
                "abstract_zh": item.get("abstract_zh"),
                "analysis_zh": item.get("analysis_zh"),
            }
        )
    for item in news[:10]:
        records.append(
            {
                "kind": "news",
                "title": item.get("title"),
                "title_zh": item.get("title_zh"),
                "content_zh": item.get("content_zh"),
                "analysis_zh": item.get("analysis_zh"),
            }
        )

    fallback_zh_parts = []
    for item in papers[:3]:
        text = clean_space(item.get("abstract_zh"))
        if text:
            fallback_zh_parts.append(text[:120])
    for item in news[:2]:
        text = clean_space(item.get("content_zh"))
        if text:
            fallback_zh_parts.append(text[:100])
    fallback_zh = "；".join(fallback_zh_parts) or "本期未获得足够的可核验证据，页面保留书目信息与来源审计。"
    fallback_en = (
        f"This issue tracks recent literature and public-health reporting about {display_en}. "
        "Claims are limited to available abstracts or fetched source text."
    )

    if not records or not llm.available:
        return {"zh": fallback_zh, "en": fallback_en, "status": "deterministic"}

    system = """You are a senior infectious-disease intelligence editor. Return JSON only.
Use only the supplied records. Do not invent case counts, conclusions, locations, dates, or causal claims.
Write one concise Chinese overview of 150-260 Chinese characters and one English overview of 80-140 words.
Distinguish peer-reviewed research, reviews, preprints, and news. Mention uncertainty and evidence limitations."""
    prompt = json.dumps(
        {
            "pathogen_zh": display_zh,
            "pathogen_en": display_en,
            "records": records,
            "required": {"zh": "string", "en": "string"},
        },
        ensure_ascii=False,
    )

    def validator(data: Any) -> tuple[bool, str]:
        if not isinstance(data, dict):
            return False, "not object"
        if len(clean_space(data.get("zh"))) < 40:
            return False, "Chinese overview too short"
        if len(clean_space(data.get("en"))) < 60:
            return False, "English overview too short"
        return True, "ok"

    try:
        result = llm.json_task(
            system=system,
            prompt=prompt,
            validator=validator,
            temperature=0.1,
            max_models_per_provider=2,
        )
        return {
            "zh": clean_space(result.data.get("zh")),
            "en": clean_space(result.data.get("en")),
            "status": f"{result.provider}:{result.model}",
        }
    except LLMError:
        return {"zh": fallback_zh, "en": fallback_en, "status": "deterministic_fallback"}
