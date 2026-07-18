from __future__ import annotations

from dataclasses import dataclass

from bs4 import BeautifulSoup

from pifactory.content import (
    _external_news_urls,
    _news_content_identity,
    apply_news_content_circuit_breaker,
    resolve_and_extract_news,
)
from pifactory.relevance import filter_post_enrichment


PROFILE = {
    "post_retrieval_relevance_rules": {
        "identity_anchor_patterns": ["hantavirus", "orthohantavirus"],
        "member_patterns": ["Hantaan virus", "Seoul virus", "Puumala virus", "Andes virus"],
        "disease_patterns": [
            "hemorrhagic fever with renal syndrome",
            "hantavirus pulmonary syndrome",
            "hantavirus cardiopulmonary syndrome",
        ],
        "context_patterns": ["rodent", "case", "outbreak", "infection", "HFRS", "HPS"],
        "excluded_entity_patterns": ["fish hantavirus"],
        "minimum_relevance_score": 6,
        "review_score_min": 3,
        "qualified_abbreviation_rules": [],
    }
}


@dataclass
class Response:
    text: str
    url: str
    headers: dict[str, str]


class FakeHTTP:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.requested: list[str] = []

    def request(self, method: str, url: str, **kwargs):
        self.requested.append(url)
        if url not in self.pages:
            raise RuntimeError(f"missing page: {url}")
        return Response(self.pages[url], url, {"Content-Type": "text/html; charset=utf-8"})


class BrokenHTTP:
    def request(self, *args, **kwargs):
        raise RuntimeError("blocked")


def test_w3c_namespace_body_is_rejected_even_when_rss_title_mentions_hantavirus(monkeypatch):
    monkeypatch.setenv("PIF_NEWS_BROWSER_ENABLED", "false")
    url = "https://publisher.example/story"
    body = " ".join([
        "The namespace name http://www.w3.org/XML/1998/namespace is bound to the prefix xml.",
        "The xml:lang and xml:space attributes are defined by the XML specification.",
        "This document describes base URI processing and reserved namespace behavior.",
    ] * 12)
    html = f"""
    <html><head><title>xml: Namespace</title>
    <link rel="canonical" href="https://www.w3.org/XML/1998/namespace"></head>
    <body><main><p>{body}</p></main></body></html>
    """
    record = resolve_and_extract_news(
        FakeHTTP({url: html}),
        {
            "news_id": "w3c-1",
            "title": "Six hantavirus cruise passengers head to Australia",
            "url": url,
            "excerpt": "Six hantavirus cruise passengers head to Australia",
        },
        PROFILE,
    )
    assert record["content_status"] in {"identity_rejected", "title_only_rejected"}
    assert not record["content"]
    attempts = record["content_audit"]["extraction_attempts"]
    assert attempts
    assert all(not row["identity_valid"] for row in attempts if row.get("structural_valid"))
    assert all("w3.org" not in requested for requested in record["content_audit"]["attempted_urls"])


def test_navigation_page_with_one_sidebar_hantavirus_link_is_rejected():
    navigation = " ".join([
        "Home World Politics Sports FIFA World Cup India train schedule Prime Minister speech Latest stories",
        "A sidebar link mentions hantavirus cases reported in another region",
        "Business markets weather entertainment technology culture contact us about us mobile applications",
    ] * 12)
    accepted, audit = _news_content_identity(
        {"title": "Health authority confirms hantavirus outbreak"},
        navigation,
        "Top Stories and Latest News",
        "https://news.example/",
        PROFILE,
    )
    assert accepted is False
    assert audit["reason"] == "weak_body_identity_and_headline_mismatch"


def test_valid_article_body_passes_body_identity_gate():
    text = " ".join([
        "The regional health authority confirmed a hantavirus infection in a rural resident.",
        "Investigators linked the hantavirus case to possible exposure to rodent droppings in a closed building.",
        "Officials advised residents to ventilate enclosed spaces and avoid sweeping rodent waste.",
    ] * 4)
    accepted, audit = _news_content_identity(
        {"title": "Health authority confirms hantavirus infection"},
        text,
        "Health authority confirms hantavirus infection",
        "https://publisher.example/health/hantavirus-infection",
        PROFILE,
    )
    assert accepted is True
    assert audit["body_identity_frequency"] >= 2


def test_publisher_page_does_not_enqueue_arbitrary_external_sidebar_links():
    html = """
    <html><head>
      <title>Health authority confirms hantavirus infection</title>
      <link rel="canonical" href="https://publisher.example/health/hantavirus-infection">
    </head><body>
      <a href="https://www.w3.org/XML/1998/namespace">XML namespace reference</a>
      <a href="https://other.example/top-story">Unrelated top story</a>
    </body></html>
    """
    urls, decisions = _external_news_urls(
        BeautifulSoup(html, "lxml"),
        html,
        "https://publisher.example/health/hantavirus-infection",
        {"title": "Health authority confirms hantavirus infection", "url": "https://publisher.example/health/hantavirus-infection"},
    )
    assert urls == ["https://publisher.example/health/hantavirus-infection"]
    assert all("w3.org" not in url for url in urls)
    assert all("other.example" not in row["url"] for row in decisions)


def test_substantive_rss_summary_still_requires_body_identity(monkeypatch):
    monkeypatch.setenv("PIF_NEWS_BROWSER_ENABLED", "false")
    summary = (
        "The regional health department reported a laboratory-confirmed hantavirus infection in a rural resident. "
        "The patient was hospitalized and an investigation focused on exposure to rodent-contaminated buildings. "
        "Officials advised residents to ventilate enclosed spaces and avoid sweeping dry rodent droppings."
    )
    accepted = resolve_and_extract_news(
        BrokenHTTP(),
        {"news_id": "rss-good", "title": "Health department reports hantavirus infection", "url": "https://news.google.com/a", "excerpt": summary},
        PROFILE,
    )
    assert accepted["content_status"] == "syndicated_summary"

    unrelated = resolve_and_extract_news(
        BrokenHTTP(),
        {
            "news_id": "rss-bad",
            "title": "Health department reports hantavirus infection",
            "url": "https://news.google.com/b",
            "excerpt": "The city council approved a transport budget after a long public meeting. Officials discussed road repairs, bus routes, and traffic signals. The plan will be reviewed again next month.",
        },
        PROFILE,
    )
    assert unrelated["content_status"] == "identity_rejected"
    assert not unrelated["content"]


def test_post_enrichment_gate_actually_drops_unrelated_news_body():
    records = [
        {
            "news_id": "good",
            "title": "Hantavirus case reported",
            "content": "A hantavirus infection was confirmed after rodent exposure. The hantavirus case remains under investigation.",
            "content_identity": {"accepted": True},
        },
        {
            "news_id": "bad",
            "title": "Hantavirus case reported",
            "content": "The XML namespace document defines xml:lang and xml:space for processors and document authors.",
            "content_identity": {"accepted": True},
        },
    ]
    retained, audit = filter_post_enrichment(records, PROFILE, "news")
    assert [row["news_id"] for row in retained] == ["good"]
    assert audit["rejected"] == 1
    assert audit["rejected_records"][0]["record_id"] == "bad"


def test_circuit_breaker_rejects_same_body_reused_by_dissimilar_headlines():
    shared = "same-content-hash"
    records = [
        {
            "news_id": "n1",
            "title": "Cruise passengers monitored after hantavirus exposure",
            "resolved_url": "https://wrong.example/common",
            "content_hash": shared,
            "content_status": "full",
            "content": "body",
        },
        {
            "news_id": "n2",
            "title": "Vaccine company shares rise after early trial",
            "resolved_url": "https://wrong.example/common",
            "content_hash": shared,
            "content_status": "full",
            "content": "body",
        },
    ]
    retained, audit = apply_news_content_circuit_breaker(records)
    assert retained == []
    assert audit["rejected"] == 2
    assert audit["groups"][0]["action"] == "reject_all_shared_error_page"


def test_circuit_breaker_keeps_one_copy_of_same_story():
    records = [
        {
            "news_id": "n1",
            "title": "Health authority confirms hantavirus case in county",
            "resolved_url": "https://publisher.example/story",
            "content_hash": "same",
            "content_status": "full",
            "content": "long body",
            "content_audit": {"selected_quality": {"chars": 2000}},
        },
        {
            "news_id": "n2",
            "title": "County health authority confirms a hantavirus case",
            "resolved_url": "https://publisher.example/story",
            "content_hash": "same",
            "content_status": "partial",
            "content": "short body",
            "content_audit": {"selected_quality": {"chars": 500}},
        },
    ]
    retained, audit = apply_news_content_circuit_breaker(records)
    assert [row["news_id"] for row in retained] == ["n1"]
    assert audit["rejected"] == 1
