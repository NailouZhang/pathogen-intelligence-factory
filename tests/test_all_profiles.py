from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]


def all_profile_ids():
    schedule = yaml.safe_load((ROOT / "config" / "weekly_virus_schedule.yaml").read_text(encoding="utf-8"))
    return [p for values in schedule["week"].values() for p in values]


def test_all_21_profiles_have_strict_seed_contracts():
    profiles = all_profile_ids()
    assert len(profiles) == 21
    assert len(set(profiles)) == 21
    for profile_id in profiles:
        path = ROOT / "profiles" / profile_id / "seed.yaml"
        assert path.is_file(), profile_id
        seed = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert seed["profile_id"] == profile_id
        assert seed["schema_version"] == "3.0"
        assert seed["source_policy"]["exact_urls_only"] is True
        assert seed["source_policy"]["allow_search_discovery"] is False
        assert seed["source_policy"]["allow_llm_memory_completion"] is False
        assert seed["candidate_vocabulary"]["identity_anchor_terms"]
        assert seed["authoritative_sources"]
        for source in seed["authoritative_sources"]:
            parsed = urlparse(source["url"])
            assert parsed.scheme == "https"
            assert parsed.hostname


def test_no_google_cse_secret_or_discovery_reference_in_production_files():
    targets = [
        ROOT / ".github/workflows/daily-intelligence.yml",
        ROOT / "src/pifactory/config.py",
        ROOT / "src/pifactory/bootstrap.py",
        ROOT / "README.md",
    ]
    for path in targets:
        text = path.read_text(encoding="utf-8")
        assert "GOOGLE_CSE_API_KEY" not in text
        assert "GOOGLE_CSE_ID" not in text
