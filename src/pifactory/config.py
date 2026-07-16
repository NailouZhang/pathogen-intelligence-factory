from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .utils import load_json


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
    window_days: int = 7
    max_papers: int = 24
    max_news: int = 36
    max_news_fetches: int = 50
    max_fulltexts: int = 18
    timezone: str = "Asia/Shanghai"

    @property
    def secrets(self) -> dict[str, str]:
        names = [
            "CROSSREF_MAILTO",
            "NCBI_API_KEY",
            "GEMINI_API_KEY",
            "GROQ_API_KEY",
            "SEMANTIC_SCHOLAR_API_KEY",
        ]
        return {name: os.getenv(name, "").strip() for name in names}

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
