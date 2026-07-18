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


@dataclass
class Settings:
    profile_id: str
    project_root: Path
    output_dir: Path
    state_dir: Path
    window_days: int = field(default_factory=lambda: env_int("PIF_WINDOW_DAYS", 7))
    max_papers: int = field(default_factory=lambda: env_int("PIF_MAX_PAPERS", 50))
    max_news: int = field(default_factory=lambda: env_int("PIF_MAX_NEWS", 50))
    # Candidate metadata is kept through deduplication and relevance review.
    # Expensive full-text/news-body enrichment is intentionally delayed until
    # the final display Top-N has been selected.
    max_paper_candidates: int = field(default_factory=lambda: env_int("PIF_MAX_PAPER_CANDIDATES", 0))
    max_news_candidates: int = field(default_factory=lambda: env_int("PIF_MAX_NEWS_CANDIDATES", 0))
    max_news_fetches: int = field(default_factory=lambda: env_int("PIF_MAX_NEWS_FETCHES", 50))
    max_fulltexts: int = field(default_factory=lambda: env_int("PIF_MAX_FULLTEXTS", 50))
    pubmed_per_query: int = field(default_factory=lambda: env_int("PIF_PUBMED_PER_QUERY", 180))
    pubmed_total_limit: int = field(default_factory=lambda: env_int("PIF_PUBMED_TOTAL_LIMIT", 2000))
    europe_pmc_per_query: int = field(default_factory=lambda: env_int("PIF_EUROPE_PMC_PER_QUERY", 150))
    crossref_per_query: int = field(default_factory=lambda: env_int("PIF_CROSSREF_PER_QUERY", 45))
    crossref_include_indexed: bool = field(default_factory=lambda: env_bool("PIF_CROSSREF_INCLUDE_INDEXED", True))
    semantic_per_query: int = field(default_factory=lambda: env_int("PIF_SEMANTIC_PER_QUERY", 80))
    semantic_anonymous_query_limit: int = field(default_factory=lambda: env_int("PIF_SEMANTIC_ANONYMOUS_QUERY_LIMIT", 5))
    semantic_anonymous_delay_ms: int = field(default_factory=lambda: env_int("PIF_SEMANTIC_ANONYMOUS_DELAY_MS", 500))
    openalex_per_query: int = field(default_factory=lambda: env_int("PIF_OPENALEX_PER_QUERY", 100))
    # LLM review has no document-count or character cutoff.  Every candidate
    # that survives the Python coarse gate is packed into token-budgeted batches
    # until the queue is empty.  Only unresolved U records receive fuller
    # sentence-selected evidence.
    llm_review_mode: str = field(default_factory=lambda: os.getenv("PIF_LLM_REVIEW_MODE", "balanced").strip().lower())
    llm_compact_batch_tokens: int = field(default_factory=lambda: env_int("PIF_LLM_COMPACT_BATCH_TOKENS", 12000))
    llm_escalation_batch_tokens: int = field(default_factory=lambda: env_int("PIF_LLM_ESCALATION_BATCH_TOKENS", 10000))
    relevance_review_cache_enabled: bool = field(default_factory=lambda: env_bool("PIF_RELEVANCE_REVIEW_CACHE", True))
    analysis_cache_enabled: bool = field(default_factory=lambda: env_bool("PIF_ANALYSIS_CACHE", True))
    news_context_query_limit: int = field(default_factory=lambda: env_int("PIF_NEWS_CONTEXT_QUERY_LIMIT", 0))
    profile_runtime_minutes: int = field(default_factory=lambda: env_int("PIF_PROFILE_RUNTIME_MINUTES", 90))
    overview_min_items: int = field(default_factory=lambda: env_int("PIF_OVERVIEW_MIN_ITEMS", 15))
    overview_max_items: int = field(default_factory=lambda: env_int("PIF_OVERVIEW_MAX_ITEMS", 25))
    wechat_news_max_zh_chars: int = field(default_factory=lambda: env_int("PIF_WECHAT_NEWS_MAX_ZH_CHARS", 500))
    display_candidate_buffer: int = field(default_factory=lambda: env_int("PIF_DISPLAY_CANDIDATE_BUFFER", 20))
    timezone: str = "Asia/Shanghai"

    @property
    def secrets(self) -> dict[str, str]:
        names = [
            "CROSSREF_MAILTO",
            "NCBI_API_KEY",
            "GEMINI_API_KEY",
            "GROQ_API_KEY",
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
