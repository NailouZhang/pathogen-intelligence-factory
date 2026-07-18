from __future__ import annotations

from pifactory.event_query import append_event_queries_to_plan


def test_event_queries_preserve_query_plan_list_contract() -> None:
    plan = [{
        "group_id": "pubmed-01",
        "provider": "pubmed",
        "query": '"hantavirus"',
        "pubmed_query": '"hantavirus"',
    }]
    event_plan = {
        "policy_version": "v14-event-driven-news-query-1",
        "queries": ['"Hantavirus" Chile outbreak 2026'],
        "evidence": [{
            "query": '"Hantavirus" Chile outbreak 2026',
            "paper_id": "paper-1",
            "title": "Chile outbreak report",
            "location": "Chile",
            "event_word": "outbreak",
        }],
    }

    result = append_event_queries_to_plan(
        plan, event_plan, scarce_news_mode=False, max_groups=120
    )

    assert result is plan
    assert isinstance(result, list)
    assert len(result) == 2
    dynamic = result[-1]
    assert dynamic["provider"] == "event_driven_news"
    assert dynamic["news_query"] == '"Hantavirus" Chile outbreak 2026'
    assert dynamic["source_paper_id"] == "paper-1"
    assert dynamic["scarce_news_mode"] is False


def test_duplicate_event_query_is_not_appended() -> None:
    query = '"Hantavirus" Chile outbreak 2026'
    plan = [{"group_id": "news-01", "provider": "news_en", "query": query}]
    result = append_event_queries_to_plan(
        plan, {"queries": [query], "evidence": []}, scarce_news_mode=False
    )
    assert result == plan
    assert len(result) == 1


def test_event_query_plan_rejects_mapping_contract_regression() -> None:
    try:
        append_event_queries_to_plan(  # type: ignore[arg-type]
            {}, {"queries": []}, scarce_news_mode=False
        )
    except TypeError as exc:
        assert "must remain a list" in str(exc)
    else:
        raise AssertionError("mapping query plan must be rejected")
