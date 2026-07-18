#!/usr/bin/env python3
from __future__ import annotations

import os


ROWS = [
    ("CROSSREF_MAILTO", "recommended", "Crossref polite pool and contact identity"),
    ("UNPAYWALL_EMAIL", "recommended", "legal open-access location lookup; may equal CROSSREF_MAILTO"),
    ("NCBI_API_KEY", "recommended", "higher PubMed E-utilities request allowance"),
    ("GEMINI_API_KEY", "required_for_full_analysis", "profile refinement, relevance review, analysis, translation and optional cover"),
    ("GROQ_API_KEY", "recommended_fallback", "text-model fallback when Gemini fails"),
    ("OPENALEX_API_KEY", "required_for_openalex", "OpenAlex currently requires an API key"),
    ("SEMANTIC_SCHOLAR_API_KEY", "optional_but_recommended", "anonymous retrieval remains enabled with conservative per-query pacing"),
    ("RELIEFWEB_APPNAME", "pending_approval", "pre-approved ReliefWeb appname; default is wiv-virology-literature-tracker-42x"),
    ("PUBLISHER_REPO_TOKEN", "required_for_wechat_dispatch", "repository_dispatch to the private publisher repository"),
]


def main() -> int:
    print("Credential/readiness audit (values are never printed)\n")
    missing_required = 0
    for name, level, purpose in ROWS:
        configured = bool(os.getenv(name, "").strip())
        if name == "RELIEFWEB_APPNAME" and not configured:
            configured = True
            note = "default configured; approval may still be pending"
        else:
            note = "configured" if configured else "not configured"
        print(f"[{note:44}] {name:28} {level:28} {purpose}")
        if level.startswith("required") and not configured:
            missing_required += 1
    print("\nSemantic Scholar without a key: the five lean provider-native queries use conservative anonymous pacing.")
    print("ReliefWeb before approval: attempted; 401/403 is recorded as pending/skipped, not as a genuine zero-result search.")
    return 2 if missing_required else 0


if __name__ == "__main__":
    raise SystemExit(main())
