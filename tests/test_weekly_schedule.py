from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.resolve_weekly_schedule import load_schedule, resolve_profiles

ROOT = Path(__file__).resolve().parents[1]
SCHEDULE = load_schedule(ROOT / "config" / "weekly_virus_schedule.yaml")


def test_schedule_has_15_unique_profiles():
    all_profiles, reason = resolve_profiles(SCHEDULE, mode="all")
    assert reason == "all-profiles"
    assert len(all_profiles) == 15
    assert len(set(all_profiles)) == 15


def test_monday_has_three_in_required_order():
    profiles, reason = resolve_profiles(
        SCHEDULE,
        mode="scheduled",
        now=datetime(2026, 7, 20, 2, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert reason == "monday"
    assert profiles == ["arenaviridae", "hantavirus", "mpox-virus"]


def test_other_days_have_two():
    for day in range(21, 27):
        profiles, _ = resolve_profiles(
            SCHEDULE,
            mode="scheduled",
            now=datetime(2026, 7, day, 2, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        assert len(profiles) == 2
