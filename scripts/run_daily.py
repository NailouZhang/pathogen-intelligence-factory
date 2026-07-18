#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pifactory.config import Settings
from src.pifactory.pipeline import run_pipeline
from src.pifactory.progress import progress


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    root = ROOT
    settings = Settings(
        profile_id=args.profile,
        project_root=root,
        output_dir=Path(args.output_dir),
        state_dir=Path(args.state_dir),
    )
    progress("run_daily", "start", profile=args.profile, output=str(args.output_dir), demo=args.demo)
    issue = run_pipeline(settings, demo=args.demo)
    progress("run_daily", "success", profile=args.profile, issue_id=issue["issue_id"], metrics=issue["metrics"])
    print({"status": "success", "issue_id": issue["issue_id"], "metrics": issue["metrics"]}, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
