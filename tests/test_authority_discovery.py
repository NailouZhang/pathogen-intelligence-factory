from pathlib import Path

import pytest
import yaml

from pifactory.authority_discovery import AuthorityDiscoveryDisabled, discover_authoritative_urls
from pifactory.authority_sources import configured_authority_sources

ROOT = Path(__file__).resolve().parents[1]


def test_search_discovery_is_permanently_disabled():
    with pytest.raises(AuthorityDiscoveryDisabled):
        discover_authoritative_urls({}, None)


def test_exact_sources_are_loaded_from_seed_only():
    seed = yaml.safe_load((ROOT / "profiles/hantavirus/seed.yaml").read_text(encoding="utf-8"))
    sources = configured_authority_sources(seed)
    assert len(sources) >= 3
    assert all(x["url"].startswith("https://") for x in sources)
    assert {x["url"] for x in sources} == {x["url"] for x in seed["authoritative_sources"]}
