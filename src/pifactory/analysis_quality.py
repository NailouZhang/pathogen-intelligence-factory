from __future__ import annotations

from collections import Counter
from typing import Any

from .utils import clean_space


FAILURE_LABELS_ZH = {
    "no_provider_configured": "未配置可用的 LLM 供应商密钥",
    "provider_not_configured": "部分模型提供方未配置",
    "authentication_failed": "API 密钥无效或认证失败",
    "rate_limited": "请求触发限流",
    "quota_exhausted": "免费额度或配额耗尽",
    "timeout": "模型请求超时",
    "context_or_output_limit": "上下文或输出长度超限",
    "invalid_json": "模型返回不是合法 JSON",
    "validation_failed": "模型 JSON 未通过结构/证据校验",
    "empty_response": "模型未返回有效候选内容",
    "provider_unavailable": "模型服务暂时不可用",
    "network_error": "模型网络请求失败",
    "model_discovery_failed": "未发现可用文本模型",
    "unknown": "未分类的模型错误",
}

FAILURE_LABELS_EN = {
    "no_provider_configured": "no configured LLM provider API key was available",
    "provider_not_configured": "one or more providers were not configured",
    "authentication_failed": "API authentication failed",
    "rate_limited": "requests were rate limited",
    "quota_exhausted": "the provider quota was exhausted",
    "timeout": "model requests timed out",
    "context_or_output_limit": "the context or output limit was exceeded",
    "invalid_json": "the model returned invalid JSON",
    "validation_failed": "model JSON failed schema or evidence validation",
    "empty_response": "the model returned no usable content",
    "provider_unavailable": "the model service was unavailable",
    "network_error": "the model network request failed",
    "model_discovery_failed": "no usable text model was discovered",
    "unknown": "an unclassified model error occurred",
}


def _group_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(clean_space((item.get("analysis") or {}).get("status")) or "missing" for item in items)
    analyzable = statuses.get("passed", 0) + statuses.get("fallback_source_extract", 0)
    fallback = statuses.get("fallback_source_extract", 0)
    passed = statuses.get("passed", 0)
    return {
        "total_items": len(items),
        "analyzable": analyzable,
        "passed": passed,
        "fallback": fallback,
        "fallback_ratio": round(fallback / analyzable, 4) if analyzable else 0.0,
        "statuses": dict(sorted(statuses.items())),
    }


def summarize_analysis_quality(
    papers: list[dict[str, Any]],
    news: list[dict[str, Any]],
    *,
    warning_ratio: float = 0.20,
    critical_ratio: float = 0.50,
    preflight: dict[str, Any] | None = None,
    scope: str = "displayed",
) -> dict[str, Any]:
    paper_summary = _group_summary(papers)
    news_summary = _group_summary(news)
    combined = _group_summary(papers + news)

    failure_counts: Counter[str] = Counter()
    attempt_failure_counts: Counter[str] = Counter()
    provider_attempts: Counter[str] = Counter()
    model_attempts: Counter[str] = Counter()
    fallback_records: list[dict[str, Any]] = []

    for item_type, items in (("paper", papers), ("news", news)):
        for item in items:
            analysis = item.get("analysis") or {}
            for attempt in analysis.get("attempts") or []:
                provider = clean_space(attempt.get("provider")) or "unknown"
                model = clean_space(attempt.get("model")) or "not_selected"
                provider_attempts[f"{provider}:{attempt.get('status') or 'unknown'}"] += 1
                model_attempts[f"{provider}/{model}:{attempt.get('status') or 'unknown'}"] += 1
                if attempt.get("status") == "failed":
                    attempt_failure_counts[clean_space(attempt.get("failure_category")) or "unknown"] += 1
            if analysis.get("status") == "fallback_source_extract":
                category = clean_space(analysis.get("failure_category")) or "unknown"
                failure_counts[category] += 1
                fallback_records.append({
                    "type": item_type,
                    "id": item.get("paper_id") or item.get("news_id"),
                    "title": item.get("title"),
                    "failure_category": category,
                    "fallback_policy": analysis.get("fallback_policy"),
                    "error": clean_space(analysis.get("error"))[:500],
                    "attempts": analysis.get("attempts") or [],
                })

    ratios = [
        group["fallback_ratio"]
        for group in (paper_summary, news_summary, combined)
        if group["analyzable"]
    ]
    max_ratio = max(ratios, default=0.0)
    if combined["analyzable"] == 0:
        severity = "unavailable"
    elif max_ratio >= critical_ratio:
        severity = "critical"
    elif max_ratio >= warning_ratio:
        severity = "warning"
    else:
        severity = "normal"

    top_failures = [
        {
            "category": category,
            "count": count,
            "label_zh": FAILURE_LABELS_ZH.get(category, category),
            "label_en": FAILURE_LABELS_EN.get(category, category),
        }
        for category, count in failure_counts.most_common(8)
    ]
    fallback_percent = round(combined["fallback_ratio"] * 100, 1)
    major_zh = "、".join(row["label_zh"] for row in top_failures[:3]) or "暂无可归类错误"
    major_en = ", ".join(row["label_en"] for row in top_failures[:3]) or "no classified failure"

    if severity == "critical":
        message_zh = (
            f"本期分析质量严重降级：{combined['fallback']}/{combined['analyzable']} 条（{fallback_percent}%）"
            f"使用低置信规则兜底。主要原因：{major_zh}。七/五要素仅可作为来源文本的辅助摘录，"
            "不应视为完整的模型精读。"
        )
        message_en = (
            f"Analysis quality is critically degraded: {combined['fallback']}/{combined['analyzable']} items "
            f"({fallback_percent}%) used low-confidence deterministic extraction. Main causes: {major_en}."
        )
    elif severity == "warning":
        message_zh = (
            f"本期有 {combined['fallback']}/{combined['analyzable']} 条（{fallback_percent}%）分析使用规则兜底。"
            f"主要原因：{major_zh}；请结合原始摘要或正文阅读。"
        )
        message_en = (
            f"{combined['fallback']}/{combined['analyzable']} analyses ({fallback_percent}%) used deterministic "
            f"fallback. Main causes: {major_en}."
        )
    elif severity == "unavailable":
        message_zh = "本期没有可执行七/五要素分析的有效证据记录。"
        message_en = "No evidence record was available for structured analysis in this issue."
    else:
        message_zh = "本期七/五要素分析未触发全局降级告警。"
        message_en = "No global structured-analysis degradation warning was triggered."

    return {
        "policy_version": "v11-analysis-quality-observability-1",
        "scope": scope,
        "severity": severity,
        "warning_ratio": warning_ratio,
        "critical_ratio": critical_ratio,
        "message_zh": message_zh,
        "message_en": message_en,
        "papers": paper_summary,
        "news": news_summary,
        "combined": combined,
        "top_failure_categories": top_failures,
        "provider_attempts": dict(sorted(provider_attempts.items())),
        "model_attempts": dict(sorted(model_attempts.items())),
        "attempt_failure_categories": dict(sorted(attempt_failure_counts.items())),
        "preflight": preflight or {},
        "fallback_records": fallback_records,
    }
