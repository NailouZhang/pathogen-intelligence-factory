from datetime import date

from src.pifactory.news import search_reliefweb
from src.pifactory.scholarly import search_crossref, search_openalex, search_semantic_scholar
from src.pifactory.source_status import SourceAudit


class FakeHttp:
    def __init__(self):
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if "crossref.org" in url:
            return {"message": {"items": []}}
        if "semanticscholar.org" in url:
            return {"data": []}
        if "openalex.org" in url:
            return {"results": [], "meta": {"next_cursor": None}}
        raise AssertionError(url)

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        raise RuntimeError("403 appname pending approval")


def test_crossref_runs_publication_channel_by_default():
    http = FakeHttp()
    audit = SourceAudit()
    search_crossref(http, ["Nipah virus"], date(2026, 7, 1), date(2026, 7, 8), "x@example.org", audit=audit)
    filters = [call[2]["params"]["filter"] for call in http.calls]
    assert any("from-pub-date" in value for value in filters)
    assert not any("from-created-date" in value for value in filters)
    assert not any("from-index-date" in value for value in filters)
    assert all("query.bibliographic" in call[2]["params"] for call in http.calls)


def test_openalex_uses_search_exact_and_normal_as_separate_parameters():
    http = FakeHttp()
    audit = SourceAudit()
    search_openalex(
        http,
        ["Nipah virus"],
        ['("Nipah virus" OR "Nipah virus infection")'],
        date(2026, 7, 1),
        date(2026, 7, 8),
        api_key="key",
        audit=audit,
    )
    params = [call[2]["params"] for call in http.calls]
    assert any("search.exact" in x and "search" not in x for x in params)
    assert any("search" in x and "search.exact" not in x for x in params)
    assert all(x["api_key"] == "key" for x in params)


def test_semantic_scholar_anonymous_mode_is_bounded():
    http = FakeHttp()
    audit = SourceAudit()
    queries = [f"virus query {i}" for i in range(20)]
    search_semantic_scholar(http, queries, date(2026, 7, 1), date(2026, 7, 8), api_key="", anonymous_query_limit=5, audit=audit)
    semantic_calls = [x for x in http.calls if "semanticscholar.org" in x[1]]
    assert len(semantic_calls) == 5
    assert all("x-api-key" not in x[2].get("headers", {}) for x in semantic_calls)


def test_reliefweb_pending_approval_is_not_reported_as_empty_success():
    http = FakeHttp()
    audit = SourceAudit()
    records = search_reliefweb(
        http,
        ["Nipah virus"],
        date(2026, 7, 1),
        date(2026, 7, 8),
        appname="wiv-virology-literature-tracker-42x",
        audit=audit,
    )
    assert records == []
    assert audit.entries[0]["status"] == "skipped"
    assert audit.entries[0]["details"]["approval_status"] == "pending_or_not_approved"
