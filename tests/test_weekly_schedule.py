from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.resolve_weekly_schedule import load_schedule, resolve_profiles

ROOT = Path(__file__).resolve().parents[1]
SCHEDULE = load_schedule(ROOT / "config" / "weekly_virus_schedule.yaml")


def test_schedule_has_21_unique_profiles_three_per_day():
    all_profiles, reason = resolve_profiles(SCHEDULE, mode="all")
    assert reason == "all-profiles"
    assert len(all_profiles) == 21
    assert len(set(all_profiles)) == 21
    assert all(len(SCHEDULE["week"][day]) == 3 for day in SCHEDULE["week"])


def test_monday_required_order():
    profiles, reason = resolve_profiles(
        SCHEDULE,
        mode="scheduled",
        now=datetime(2026, 7, 20, 2, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert reason == "monday"
    assert profiles == ["seasonal_influenza", "sars_cov_2", "respiratory_syncytial_virus"]


def test_each_remaining_day_has_three():
    for day in range(21, 27):
        profiles, _ = resolve_profiles(
            SCHEDULE,
            mode="scheduled",
            now=datetime(2026, 7, day, 2, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        assert len(profiles) == 3
