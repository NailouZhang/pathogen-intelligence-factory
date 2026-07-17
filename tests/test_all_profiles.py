from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_all_scheduled_profiles_have_valid_seed_files():
    schedule = yaml.safe_load((ROOT / "config" / "weekly_virus_schedule.yaml").read_text(encoding="utf-8"))
    profiles = [p for values in schedule["week"].values() for p in values]
    for profile_id in profiles:
        path = ROOT / "profiles" / profile_id / "seed.yaml"
        assert path.is_file(), profile_id
        seed = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert seed["profile_id"] == profile_id
        assert len(seed["seed_terms"]) >= 2
        assert len(seed["query_groups"]) >= 7
        assert seed["display_name_zh"]
