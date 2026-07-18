from pathlib import Path


def test_workflow_streams_python_output_and_enriches_only_top50():
    text = Path(".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    assert "python -u scripts/run_daily.py" in text
    assert '| tee "/tmp/${PROFILE_ID}.combined.log"' in text
    assert 'PIF_MAX_NEWS_FETCHES: "50"' in text
    assert 'PIF_MAX_FULLTEXTS: "50"' in text
    assert 'PIF_SEMANTIC_ANONYMOUS_QUERY_LIMIT: "5"' in text
    assert "timeout --signal=TERM" in text


def test_pipeline_selects_top_n_before_network_content_enrichment():
    text = Path("src/pifactory/pipeline.py").read_text(encoding="utf-8")
    selection = text.index("papers = rank_papers(papers)[: settings.max_papers]")
    paper_enrichment = text.index("enrich_scholarly_work")
    news_selection = text.index("news = rank_news(news)[: settings.max_news]")
    news_enrichment = text.index("resolve_and_extract_news")
    # Imports appear earlier, so inspect the calls after the selection marker.
    assert text.index("lambda item: enrich_scholarly_work", selection) > selection
    assert text.index("lambda item: resolve_and_extract_news", news_selection) > news_selection
