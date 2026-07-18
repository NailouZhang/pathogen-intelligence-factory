from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_workflow_exposes_v7_provider_credentials_and_balanced_review():
    text = (ROOT / ".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    assert "OPENALEX_API_KEY" in text
    assert "wiv-virology-literature-tracker-42x" in text
    assert 'PIF_MAX_PAPER_CANDIDATES: "0"' in text
    assert 'PIF_MAX_NEWS_CANDIDATES: "0"' in text
    assert "PIF_LLM_REVIEW_MODE:" in text
    assert "all_compact" in text
    assert "balanced" in text
    assert "PIF_LLM_COMPACT_BATCH_TOKENS" in text
    assert "PIF_MAX_LLM_REVIEW_PAPERS" not in text
    assert "PIF_LLM_REVIEW_BODY_CHARS" not in text
