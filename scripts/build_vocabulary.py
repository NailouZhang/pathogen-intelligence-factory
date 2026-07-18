#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pifactory.bootstrap import build_profile
from pifactory.config import Settings
from pifactory.http import HttpClient
from pifactory.llm import LLMRouter
from pifactory.utils import dump_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Build one strict v3 retrieval vocabulary from fixed authority URLs")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--state-root", type=Path, default=ROOT / ".local-state")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = Settings(
        profile_id=args.profile,
        project_root=ROOT,
        output_dir=args.state_root / "output" / args.profile,
        state_dir=args.state_root / "data" / "state",
    )
    http = HttpClient(settings.user_agent)
    llm = LLMRouter(
        http,
        gemini_key=os.getenv("GEMINI_API_KEY", "").strip(),
        groq_key=os.getenv("GROQ_API_KEY", "").strip(),
    )
    profile = build_profile(settings, http, llm)
    target = args.output or args.state_root / "profiles" / args.profile / "profile.json"
    dump_json(target, profile)
    print(target)
    print({"status": profile.get("status"), "generated_by": profile.get("generated_by"), "blocking_issues": profile.get("blocking_issues")})
    return 0 if profile.get("status") == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
