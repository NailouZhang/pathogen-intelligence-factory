from datetime import date

from src.pifactory.scholarly import probe_europe_pmc_anchor_counts, probe_pubmed_anchor_counts
from src.pifactory.source_status import SourceAudit


class Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self):
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if "esearch.fcgi" in url:
            return {"esearchresult": {"count": "7", "idlist": []}}
        if "europepmc" in url:
            return {"hitCount": 5, "resultList": {"result": []}}
        raise AssertionError(url)

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if "esearch.fcgi" in url:
            return Response({"esearchresult": {"count": "7", "idlist": []}})
        raise AssertionError(url)


def test_pubmed_probe_is_count_only_and_audited():
    http = FakeHttp()
    audit = SourceAudit()
    counts = probe_pubmed_anchor_counts(
        http, ['"Nipah virus"[Title/Abstract]'], date(2026, 4, 1), date(2026, 6, 29), audit=audit
    )
    assert list(counts.values()) == [7]
    assert audit.entries[0]["details"]["diagnostic_only"] is True
    assert audit.entries[0]["source"] == "PubMed 90-day anchor probe"


def test_europe_pmc_probe_uses_hit_count_and_never_returns_records():
    http = FakeHttp()
    audit = SourceAudit()
    counts = probe_europe_pmc_anchor_counts(
        http, ['TITLE_ABS:"Nipah virus"'], date(2026, 4, 1), date(2026, 6, 29), audit=audit
    )
    assert list(counts.values()) == [5]
    assert audit.entries[0]["details"]["diagnostic_only"] is True
    params = http.calls[0][2]["params"]
    assert params["pageSize"] == 1
