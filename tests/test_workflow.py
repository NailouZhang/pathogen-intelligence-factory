from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_workflow_runs_at_beijing_0200_and_has_no_google_cse():
    text = (ROOT / ".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    assert "cron: '0 18 * * *'" in text or 'cron: "0 18 * * *"' in text
    assert "deploy-pages@v4" in text
    assert "intelligence-data" in text
    assert "GOOGLE_CSE" not in text
    assert "for PROFILE_ID" in text
