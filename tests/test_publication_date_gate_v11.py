from datetime import date

from pifactory.dates import assess_publication_date, date_window, publication_search_end
from pifactory.scholarly import _date_from_parts, _pubmed_term, filter_publication_window, search_crossref, search_europe_pmc


class FakeHttp:
    def __init__(self):
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "crossref.org" in url:
            return {"message": {"items": []}}
        if "europepmc" in url:
            return {"resultList": {"result": []}, "nextCursorMark": None}
        raise AssertionError(url)



def test_source_date_parts_preserve_precision():
    assert _date_from_parts(2026) == "2026"
    assert _date_from_parts(2026, 7) == "2026-07"
    assert _date_from_parts(2026, 7, 15) == "2026-07-15"

def test_date_window_is_exactly_seven_inclusive_calendar_days():
    start, end = date_window(7, end=date(2026, 7, 18))
    assert start == date(2026, 7, 12)
    assert end == date(2026, 7, 18)


def test_recent_index_date_cannot_revive_old_publication():
    decision = assess_publication_date(
        {
            "online_date": "2011-11-16",
            "published_date": "2011-12-01",
            "indexed_date": "2026-07-17",
            "created_date": "2026-07-16",
        },
        date(2026, 7, 12),
        date(2026, 7, 18),
        future_days=90,
    )
    assert decision.accepted is False
    assert decision.reason == "real_publication_date_before_window"
    assert decision.metadata_dates["indexed_date"] == "2026-07-17"


def test_metadata_only_record_is_rejected():
    decision = assess_publication_date(
        {"created_date": "2026-07-16", "indexed_date": "2026-07-17"},
        date(2026, 7, 12),
        date(2026, 7, 18),
        future_days=90,
    )
    assert decision.accepted is False
    assert decision.reason == "missing_real_publication_date"


def test_current_online_date_accepts_future_print_assignment():
    decision = assess_publication_date(
        {"online_date": "2026-07-15", "print_date": "2026-09-01", "indexed_date": "2026-07-16"},
        date(2026, 7, 12),
        date(2026, 7, 18),
        future_days=90,
    )
    assert decision.accepted is True
    assert decision.canonical_date == "2026-07-15"
    assert decision.canonical_basis == "online_date"
    assert decision.status == "in_window"


def test_future_print_only_is_allowed_within_bounded_grace():
    decision = assess_publication_date(
        {"print_date": "2026-08-15", "created_date": "2026-07-16"},
        date(2026, 7, 12),
        date(2026, 7, 18),
        future_days=90,
    )
    assert decision.accepted is True
    assert decision.status == "future_scheduled"
    assert decision.canonical_basis == "print_date"


def test_far_future_publication_is_rejected():
    decision = assess_publication_date(
        {"print_date": "2027-03-01", "indexed_date": "2026-07-16"},
        date(2026, 7, 12),
        date(2026, 7, 18),
        future_days=90,
    )
    assert decision.accepted is False
    assert decision.reason == "real_publication_date_beyond_future_grace"


def test_year_only_publication_date_cannot_prove_weekly_recency():
    decision = assess_publication_date(
        {"published_date": "2026", "indexed_date": "2026-07-16"},
        date(2026, 7, 12),
        date(2026, 7, 18),
        future_days=90,
    )
    assert decision.accepted is False
    assert decision.reason == "real_publication_date_too_imprecise"


def test_filter_returns_rejected_records_for_audit():
    accepted, rejected = filter_publication_window(
        [
            {"title": "new", "online_date": "2026-07-14"},
            {"title": "old", "published_date": "2013-11-08", "indexed_date": "2026-07-17"},
        ],
        date(2026, 7, 12),
        date(2026, 7, 18),
        future_days=90,
    )
    assert [row["title"] for row in accepted] == ["new"]
    assert [row["title"] for row in rejected] == ["old"]
    assert rejected[0]["publication_date_gate"]["reason"] == "real_publication_date_before_window"


def test_pubmed_query_uses_publication_dates_not_processing_dates():
    term = _pubmed_term("hantavirus", date(2026, 7, 12), date(2026, 10, 16))
    assert "EPDAT" in term
    assert "PPDAT" in term
    assert "PDAT" in term
    assert "CRDT" not in term
    assert "EDAT" not in term


def test_europe_pmc_query_uses_first_publication_date_only():
    http = FakeHttp()
    search_europe_pmc(http, ["hantavirus"], date(2026, 7, 12), date(2026, 10, 16), per_query=1)
    query = http.calls[0][1]["params"]["query"]
    assert "FIRST_PDATE" in query
    assert "CREATION_DATE" not in query


def test_crossref_defaults_to_publication_channel_only():
    http = FakeHttp()
    search_crossref(http, ["hantavirus"], date(2026, 7, 12), publication_search_end(date(2026, 7, 18), 90), "x@example.org")
    filters = [call[1]["params"]["filter"] for call in http.calls]
    assert any("from-pub-date" in value for value in filters)
    assert not any("from-index-date" in value for value in filters)
