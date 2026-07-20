from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .utils import load_json



def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc


@dataclass
class Settings:
    profile_id: str
    project_root: Path
    output_dir: Path
    state_dir: Path
    window_days: int = field(default_factory=lambda: env_int("PIF_WINDOW_DAYS", 7))
    # Some journals assign a future issue/print date to papers already visible
    # this week. Search is allowed to extend a bounded number of days forward,
    # but admission still requires a real publication field.
    publication_future_days: int = field(default_factory=lambda: env_int("PIF_PUBLICATION_FUTURE_DAYS", 90))
    max_papers: int = field(default_factory=lambda: env_int("PIF_MAX_PAPERS", 50))
    max_news: int = field(default_factory=lambda: env_int("PIF_MAX_NEWS", 50))
    # Candidate metadata is kept through deduplication and relevance review.
    # Expensive full-text/news-body enrichment is intentionally delayed until
    # the final display Top-N has been selected.
    max_paper_candidates: int = field(default_factory=lambda: env_int("PIF_MAX_PAPER_CANDIDATES", 0))
    max_news_candidates: int = field(default_factory=lambda: env_int("PIF_MAX_NEWS_CANDIDATES", 0))
    max_news_fetches: int = field(default_factory=lambda: env_int("PIF_MAX_NEWS_FETCHES", 80))
    max_fulltexts: int = field(default_factory=lambda: env_int("PIF_MAX_FULLTEXTS", 150))
    pubmed_per_query: int = field(default_factory=lambda: env_int("PIF_PUBMED_PER_QUERY", 180))
    pubmed_total_limit: int = field(default_factory=lambda: env_int("PIF_PUBMED_TOTAL_LIMIT", 2000))
    europe_pmc_per_query: int = field(default_factory=lambda: env_int("PIF_EUROPE_PMC_PER_QUERY", 150))
    crossref_per_query: int = field(default_factory=lambda: env_int("PIF_CROSSREF_PER_QUERY", 45))
    crossref_include_indexed: bool = field(default_factory=lambda: env_bool("PIF_CROSSREF_INCLUDE_INDEXED", False))
    semantic_per_query: int = field(default_factory=lambda: env_int("PIF_SEMANTIC_PER_QUERY", 80))
    semantic_anonymous_query_limit: int = field(default_factory=lambda: env_int("PIF_SEMANTIC_ANONYMOUS_QUERY_LIMIT", 5))
    semantic_anonymous_delay_ms: int = field(default_factory=lambda: env_int("PIF_SEMANTIC_ANONYMOUS_DELAY_MS", 500))
    openalex_per_query: int = field(default_factory=lambda: env_int("PIF_OPENALEX_PER_QUERY", 100))
    # The bioRxiv/medRxiv date API is a bulk feed, not a search endpoint.
    # Bound the number of downloaded records per server and apply a local
    # title/abstract identity filter before records reach deduplication.
    preprint_max_records_per_server: int = field(default_factory=lambda: env_int("PIF_PREPRINT_MAX_RECORDS_PER_SERVER", 300))
    preprint_identity_filter_enabled: bool = field(default_factory=lambda: env_bool("PIF_PREPRINT_IDENTITY_FILTER", True))
    # LLM review has no document-count or character cutoff.  Every candidate
    # that survives the Python coarse gate is packed into token-budgeted batches
    # until the queue is empty.  Only unresolved U records receive fuller
    # sentence-selected evidence.
    llm_review_mode: str = field(default_factory=lambda: os.getenv("PIF_LLM_REVIEW_MODE", "balanced").strip().lower())
    llm_compact_batch_tokens: int = field(default_factory=lambda: env_int("PIF_LLM_COMPACT_BATCH_TOKENS", 12000))
    llm_escalation_batch_tokens: int = field(default_factory=lambda: env_int("PIF_LLM_ESCALATION_BATCH_TOKENS", 10000))
    relevance_review_cache_enabled: bool = field(default_factory=lambda: env_bool("PIF_RELEVANCE_REVIEW_CACHE", True))
    analysis_cache_enabled: bool = field(default_factory=lambda: env_bool("PIF_ANALYSIS_CACHE", True))
    analysis_fallback_warning_ratio: float = field(default_factory=lambda: env_float("PIF_ANALYSIS_FALLBACK_WARNING_RATIO", 0.20))
    analysis_fallback_critical_ratio: float = field(default_factory=lambda: env_float("PIF_ANALYSIS_FALLBACK_CRITICAL_RATIO", 0.50))
    analysis_require_llm: bool = field(default_factory=lambda: env_bool("PIF_ANALYSIS_REQUIRE_LLM", False))
    # Low-token tiering: every paper receives abstract analysis; only the top
    # subset receives locally selected full-text evidence and cross-provider verification.
    analysis_fulltext_top_n: int = field(default_factory=lambda: env_int("PIF_ANALYSIS_FULLTEXT_TOP_N", 12))
    analysis_crosscheck_top_n: int = field(default_factory=lambda: env_int("PIF_ANALYSIS_CROSSCHECK_TOP_N", 5))
    analysis_evidence_max_chars: int = field(default_factory=lambda: env_int("PIF_ANALYSIS_EVIDENCE_MAX_CHARS", 9000))
    analysis_cache_success_only: bool = field(default_factory=lambda: env_bool("PIF_LLM_CACHE_SUCCESS_ONLY", True))
    llm_preflight_file: str = field(default_factory=lambda: os.getenv("PIF_LLM_PREFLIGHT_FILE", "").strip())
    news_context_query_limit: int = field(default_factory=lambda: env_int("PIF_NEWS_CONTEXT_QUERY_LIMIT", 0))
    news_event_query_limit: int = field(default_factory=lambda: env_int("PIF_NEWS_EVENT_QUERY_LIMIT", 4))
    profile_runtime_minutes: int = field(default_factory=lambda: env_int("PIF_PROFILE_RUNTIME_MINUTES", 150))
    finalization_reserve_minutes: int = field(default_factory=lambda: env_int("PIF_FINALIZATION_RESERVE_MINUTES", 30))
    retrieval_max_minutes: int = field(default_factory=lambda: env_int("PIF_RETRIEVAL_MAX_MINUTES", 15))
    relevance_max_minutes: int = field(default_factory=lambda: env_int("PIF_RELEVANCE_MAX_MINUTES", 25))
    paper_processing_max_minutes: int = field(default_factory=lambda: env_int("PIF_PAPER_PROCESSING_MAX_MINUTES", 90))
    supplementary_review_max_minutes: int = field(default_factory=lambda: env_int("PIF_SUPPLEMENTARY_REVIEW_MAX_MINUTES", 5))
    news_enrichment_max_minutes: int = field(default_factory=lambda: env_int("PIF_NEWS_ENRICHMENT_MAX_MINUTES", 10))
    news_analysis_max_minutes: int = field(default_factory=lambda: env_int("PIF_NEWS_ANALYSIS_MAX_MINUTES", 30))
    paper_analysis_attempt_multiplier: float = field(default_factory=lambda: env_float("PIF_PAPER_ANALYSIS_ATTEMPT_MULTIPLIER", 1.5))
    max_supplementary_news: int = field(default_factory=lambda: env_int("PIF_MAX_SUPPLEMENTARY_NEWS", 100))
    news_brief_min_source_chars: int = field(default_factory=lambda: env_int("PIF_NEWS_BRIEF_MIN_SOURCE_CHARS", 500))
    wechat_max_visible_chars: int = field(default_factory=lambda: env_int("PIF_WECHAT_MAX_VISIBLE_CHARS", 48000))
    wechat_min_full_papers: int = field(default_factory=lambda: env_int("PIF_WECHAT_MIN_FULL_PAPERS", 10))
    wechat_remove_supplementary_news_excerpts: bool = field(default_factory=lambda: env_bool("PIF_WECHAT_REMOVE_SUPPLEMENTARY_NEWS_EXCERPTS", True))
    wechat_allow_supplementary_paper_omission: bool = field(default_factory=lambda: env_bool("PIF_WECHAT_ALLOW_SUPPLEMENTARY_PAPER_OMISSION", True))
    wechat_min_supplementary_papers: int = field(default_factory=lambda: env_int("PIF_WECHAT_MIN_SUPPLEMENTARY_PAPERS", 0))
    wechat_allow_supplementary_news_omission: bool = field(default_factory=lambda: env_bool("PIF_WECHAT_ALLOW_SUPPLEMENTARY_NEWS_OMISSION", True))
    wechat_min_supplementary_news: int = field(default_factory=lambda: env_int("PIF_WECHAT_MIN_SUPPLEMENTARY_NEWS", 0))
    wechat_allow_main_news_omission: bool = field(default_factory=lambda: env_bool("PIF_WECHAT_ALLOW_MAIN_NEWS_OMISSION", True))
    wechat_min_main_news: int = field(default_factory=lambda: env_int("PIF_WECHAT_MIN_MAIN_NEWS", 10))
    wechat_allow_primary_paper_omission: bool = field(default_factory=lambda: env_bool("PIF_WECHAT_ALLOW_PRIMARY_PAPER_OMISSION", True))
    wechat_min_primary_papers: int = field(default_factory=lambda: env_int("PIF_WECHAT_MIN_PRIMARY_PAPERS", 10))
    wechat_title_max_chars: int = field(default_factory=lambda: env_int("PIF_WECHAT_TITLE_MAX_CHARS", 220))
    wechat_authors_max_chars: int = field(default_factory=lambda: env_int("PIF_WECHAT_AUTHORS_MAX_CHARS", 300))
    wechat_paper_abstract_max_chars: int = field(default_factory=lambda: env_int("PIF_WECHAT_PAPER_ABSTRACT_MAX_CHARS", 500))
    wechat_paper_element_max_chars: int = field(default_factory=lambda: env_int("PIF_WECHAT_PAPER_ELEMENT_MAX_CHARS", 120))
    wechat_news_element_max_chars: int = field(default_factory=lambda: env_int("PIF_WECHAT_NEWS_ELEMENT_MAX_CHARS", 100))
    wechat_overview_max_chars: int = field(default_factory=lambda: env_int("PIF_WECHAT_OVERVIEW_MAX_CHARS", 1200))
    overview_min_items: int = field(default_factory=lambda: env_int("PIF_OVERVIEW_MIN_ITEMS", 15))
    overview_max_items: int = field(default_factory=lambda: env_int("PIF_OVERVIEW_MAX_ITEMS", 25))
    wechat_news_max_zh_chars: int = field(default_factory=lambda: env_int("PIF_WECHAT_NEWS_MAX_ZH_CHARS", 500))
    display_candidate_buffer: int = field(default_factory=lambda: env_int("PIF_DISPLAY_CANDIDATE_BUFFER", 100))
    max_supplementary_papers: int = field(default_factory=lambda: env_int("PIF_MAX_SUPPLEMENTARY_PAPERS", 100))
    fulltext_batch_size: int = field(default_factory=lambda: env_int("PIF_FULLTEXT_BATCH_SIZE", 25))
    timezone: str = "Asia/Shanghai"

    @property
    def secrets(self) -> dict[str, str]:
        names = [
            "CROSSREF_MAILTO",
            "NCBI_API_KEY",
            "GEMINI_API_KEY",
            "GROQ_API_KEY",
            "OPENROUTER_API_KEY",
            "MISTRAL_API_KEY",
            "SILICONFLOW_API_KEY",
            "BIGMODEL_API_KEY",
            "DEEPSEEK_API_KEY",
            "SEMANTIC_SCHOLAR_API_KEY",
            "OPENALEX_API_KEY",
            "RELIEFWEB_APPNAME",
            "UNPAYWALL_EMAIL",
        ]
        values = {name: os.getenv(name, "").strip() for name in names}
        # ReliefWeb requires a pre-approved appname. The requested name is
        # shipped as a non-secret default; before approval the adapter records
        # a clear pending/rejected status and the remaining news sources run.
        values["RELIEFWEB_APPNAME"] = values.get("RELIEFWEB_APPNAME") or "wiv-virology-literature-tracker-42x"
        return values

    @property
    def user_agent(self) -> str:
        email = self.secrets.get("CROSSREF_MAILTO") or "contact@example.org"
        return os.getenv(
            "PIF_USER_AGENT",
            f"PathogenIntelligenceFactory/1.0 ({email})",
        )

    @property
    def refresh_profile(self) -> bool:
        return env_bool("PIF_REFRESH_PROFILE", False)

    @property
    def refresh_review_vocabulary(self) -> bool:
        return env_bool("PIF_REFRESH_REVIEW_VOCABULARY", False)

    @property
    def cover_image_mode(self) -> str:
        return os.getenv("PIF_COVER_IMAGE_MODE", "auto").strip().lower()

    @property
    def cover_image_model(self) -> str:
        return os.getenv(
            "GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image"
        ).strip()

    @property
    def publisher_repo(self) -> str:
        return os.getenv(
            "PUBLISHER_REPO", "NailouZhang/pathogen-wechat-publisher"
        ).strip()


def load_seed(project_root: Path, profile_id: str) -> dict[str, Any]:
    path = project_root / "profiles" / profile_id / "seed.yaml"
    if not path.exists():
        return {
            "profile_id": profile_id,
            "display_name_en": profile_id,
            "display_name_zh": profile_id,
            "seed_terms": [profile_id],
            "authoritative_urls": [],
        }
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("profile_id", profile_id)
    terms = data.get("seed_terms") or data.get("terms") or [profile_id]
    data["seed_terms"] = [str(x).strip() for x in terms if str(x).strip()]
    return data


def load_profile(settings: Settings) -> dict[str, Any] | None:
    persisted = (
        settings.state_dir.parent
        / "profiles"
        / settings.profile_id
        / "profile.json"
    )
    bundled = (
        settings.project_root
        / "profiles"
        / settings.profile_id
        / "profile.json"
    )
    return load_json(persisted) or load_json(bundled)
