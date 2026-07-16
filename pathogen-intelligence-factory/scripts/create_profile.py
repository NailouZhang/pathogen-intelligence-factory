#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    return value.strip("-")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a minimal pathogen profile seed")
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--term", action="append", required=True, help="Repeat for one or more pathogen terms")
    parser.add_argument("--name-en", default="")
    parser.add_argument("--name-zh", default="")
    parser.add_argument("--url", action="append", default=[])
    args = parser.parse_args()

    profile_id = slugify(args.profile_id)
    if not profile_id:
        raise SystemExit("Invalid profile id")
    target = ROOT / "profiles" / profile_id / "seed.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "profile_id": profile_id,
        "seed_terms": [x.strip() for x in args.term if x.strip()],
        "display_name_en": args.name_en.strip() or args.term[0].strip(),
        "display_name_zh": args.name_zh.strip() or args.term[0].strip(),
        "authoritative_urls": [x.strip() for x in args.url if x.strip()],
        "negative_terms": [],
    }
    target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
