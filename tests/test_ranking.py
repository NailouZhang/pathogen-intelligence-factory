from pifactory.ranking import rank_news, rank_papers


def test_high_level_paper_ranks_above_incomplete_preprint():
    records = [
        {"title":"A", "source":"medRxiv", "relevance_score":0.8, "publication_types":["preprint"]},
        {"title":"B", "source":"PubMed", "relevance_score":0.8, "publication_types":["Systematic Review"], "abstract":"complete", "doi":"10.1/x"},
    ]
    ranked = rank_papers(records)
    assert ranked[0]["title"] == "B"
    assert ranked[0]["quality_score"] > ranked[1]["quality_score"]


def test_official_news_ranks_above_general_media():
    records = [
        {"title":"media", "url":"https://example.com/a", "publisher":"Media", "relevance_score":0.9},
        {"title":"official", "url":"https://www.who.int/a", "publisher":"WHO", "relevance_score":0.8, "official":True, "content":"body", "content_status":"full"},
    ]
    ranked = rank_news(records)
    assert ranked[0]["title"] == "official"
