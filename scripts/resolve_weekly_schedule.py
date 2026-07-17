#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
DAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def load_schedule(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    week = data.get("week") or {}
    missing = [day for day in DAY_NAMES if day not in week]
    if missing:
        raise RuntimeError(f"weekly schedule missing days: {missing}")
    flattened = [str(p).strip() for day in DAY_NAMES for p in week.get(day, []) if str(p).strip()]
    if len(flattened) != len(set(flattened)):
        duplicates = sorted({x for x in flattened if flattened.count(x) > 1})
        raise RuntimeError(f"profiles occur more than once in the weekly schedule: {duplicates}")
    return data


def resolve_profiles(
    schedule: dict,
    *,
    mode: str,
    profile_id: str = "",
    profiles_csv: str = "",
    now: datetime | None = None,
) -> tuple[list[str], str]:
    timezone = str(schedule.get("timezone") or "Asia/Shanghai")
    local_now = now or datetime.now(ZoneInfo(timezone))
    week = schedule["week"]

    if profile_id.strip():
        return [profile_id.strip()], "single-profile"

    custom = [x.strip() for x in profiles_csv.split(",") if x.strip()]
    if custom:
        if len(custom) != len(set(custom)):
            raise RuntimeError("custom profile list contains duplicates")
        return custom, "custom-list"

    if mode == "all":
        return [str(p) for day in DAY_NAMES for p in week[day]], "all-profiles"

    day = DAY_NAMES[local_now.weekday()]
    return [str(p) for p in week[day]], day


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "weekly_virus_schedule.yaml",
    )
    parser.add_argument("--mode", choices=["scheduled", "all"], default="scheduled")
    parser.add_argument("--profile-id", default="")
    parser.add_argument("--profiles", default="")
    parser.add_argument("--date", default="", help="Optional YYYY-MM-DD in configured timezone")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    schedule = load_schedule(args.config)
    tz = ZoneInfo(str(schedule.get("timezone") or "Asia/Shanghai"))
    now = None
    if args.date:
        now = datetime.fromisoformat(args.date).replace(tzinfo=tz)

    profiles, reason = resolve_profiles(
        schedule,
        mode=args.mode,
        profile_id=args.profile_id,
        profiles_csv=args.profiles,
        now=now,
    )
    payload = {
        "profiles": profiles,
        "profiles_json": json.dumps(profiles, ensure_ascii=False),
        "reason": reason,
        "timezone": str(schedule.get("timezone") or "Asia/Shanghai"),
        "run_time": str(schedule.get("run_time") or "02:00"),
        "window_days": int(schedule.get("window_days") or 7),
        "max_papers": int(schedule.get("max_papers") or 50),
        "max_news": int(schedule.get("max_news") or 50),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            for key in (
                "profiles_json",
                "reason",
                "timezone",
                "run_time",
                "window_days",
                "max_papers",
                "max_news",
            ):
                print(f"{key}={payload[key]}", file=handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
