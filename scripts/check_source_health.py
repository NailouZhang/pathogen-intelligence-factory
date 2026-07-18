#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect source_status.json and fail only on genuine source failures, not valid zero-result queries.")
    parser.add_argument("path", help="Path to data/audit/source_status.json")
    parser.add_argument("--fail-on-any-failed-source", action="store_true")
    args = parser.parse_args()
    payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
    print(json.dumps({"overall": payload.get("overall"), "sources": payload.get("sources")}, ensure_ascii=False, indent=2))
    failed = [x for x in payload.get("sources") or [] if x.get("health") == "failed"]
    if args.fail_on_any_failed_source and failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
