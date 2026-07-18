from pifactory.ranking import rank_news, rank_papers


def test_papers_receive_deterministic_priority_tiers():
    records = [
        {"title": "preprint", "source": "medRxiv", "relevance_score": 0.7, "publication_types": ["preprint"]},
        {"title": "trial", "source": "PubMed", "relevance_score": 0.8, "publication_types": ["Clinical Trial"], "abstract": "evidence", "doi": "10.1/x"},
    ]
    ranked = rank_papers(records)
    assert ranked[0]["title"] == "trial"
    assert ranked[0]["priority_tier"] == "A"
    assert ranked[1]["priority_tier"] in {"B", "C"}


def test_news_receive_source_authority_tiers():
    records = [
        {"title": "media", "url": "https://example.com/a", "publisher": "Media", "relevance_score": 0.9},
        {"title": "official", "url": "https://www.who.int/a", "publisher": "WHO", "relevance_score": 0.8, "official": True, "content": "body", "content_status": "full"},
    ]
    ranked = rank_news(records)
    assert ranked[0]["priority_tier"] == "A"
    assert ranked[-1]["priority_tier"] == "C"
