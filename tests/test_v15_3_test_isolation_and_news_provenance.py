from __future__ import annotations

from pathlib import Path

from pifactory import content


class BrokenHTTP:
    def request(self, *args, **kwargs):
        raise RuntimeError("network unavailable")


def test_public_repo_contract_does_not_depend_on_bundle_parent():
    repo_root = Path(__file__).resolve().parents[1]
    assert (repo_root / "pyproject.toml").is_file()
    workflow = (repo_root / ".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    assert "inputs.refresh_profile || 'false'" in workflow


def test_unresolved_aggregator_render_is_not_promoted_to_full(monkeypatch):
    monkeypatch.setenv("PIF_NEWS_BROWSER_ENABLED", "true")
    html = """
    <html><head><title>Google News result</title></head><body><main>
    <p>The regional health department reported a laboratory-confirmed hantavirus infection.</p>
    <p>The patient was hospitalized and officials began a rodent exposure investigation.</p>
    </main></body></html>
    """
    monkeypatch.setattr(content, "fetch_rendered_html", lambda url: {
        "status": "success", "url": url, "title": "Google News result", "html": html
    })
    summary = (
        "The regional health department reported a laboratory-confirmed hantavirus infection in a rural resident. "
        "The patient was hospitalized and an exposure investigation focused on rodent-contaminated buildings. "
        "Officials advised residents to ventilate enclosed spaces and avoid sweeping dry rodent droppings."
    )
    record = content.resolve_and_extract_news(BrokenHTTP(), {
        "news_id": "rss-1",
        "title": "Health department reports hantavirus infection",
        "url": "https://news.google.com/articles/example",
        "excerpt": summary,
    })
    assert record["content_status"] == "syndicated_summary"
    assert record["content_method"] == "rss_syndicated_summary"
    assert any(
        row.get("rejection_reason") == "unresolved_aggregator_landing"
        for row in record["content_audit"]["extraction_attempts"]
    )


def test_direct_publisher_browser_body_remains_eligible(monkeypatch):
    monkeypatch.setenv("PIF_NEWS_BROWSER_ENABLED", "true")
    body = " ".join([
        "The health authority confirmed a hantavirus infection in a rural resident.",
        "Investigators linked the infection to rodent-contaminated buildings and began contact follow-up.",
        "Officials advised residents to ventilate enclosed spaces and avoid sweeping rodent droppings.",
    ] * 8)
    html = f"<html><head><title>Confirmed hantavirus infection</title></head><body><article>{body}</article></body></html>"
    monkeypatch.setattr(content, "fetch_rendered_html", lambda url: {
        "status": "success", "url": "https://health.example/report",
        "title": "Confirmed hantavirus infection", "html": html
    })
    record = content.resolve_and_extract_news(BrokenHTTP(), {
        "news_id": "publisher-1",
        "title": "Confirmed hantavirus infection",
        "url": "https://health.example/report",
        "excerpt": "Confirmed hantavirus infection",
    })
    assert record["content_status"] in {"full", "partial"}
    assert record["content_method"].startswith("playwright:")
