from pathlib import Path


def test_workflow_streams_python_output_and_bounds_display_enrichment():
    text = Path(".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    assert "python -u scripts/run_daily.py" in text
    assert '| tee "/tmp/${PROFILE_ID}.combined.log"' in text
    # v8 enriches the Top 50 plus a bounded 20-record replacement queue.
    assert 'PIF_MAX_NEWS_FETCHES: "70"' in text
    assert 'PIF_MAX_FULLTEXTS: "70"' in text
    assert 'PIF_DISPLAY_CANDIDATE_BUFFER: "20"' in text
    assert 'PIF_SEMANTIC_ANONYMOUS_QUERY_LIMIT: "5"' in text
    assert "timeout --signal=TERM" in text


def test_pipeline_selects_bounded_display_queue_before_network_content_enrichment():
    text = Path("src/pifactory/pipeline.py").read_text(encoding="utf-8")
    queue_selection = text.index("paper_queue = rank_papers(papers)")
    news_queue_selection = text.index("news_queue = rank_news(news)")
    paper_call = text.index("lambda item: enrich_scholarly_work", queue_selection)
    news_call = text.index("lambda item: resolve_and_extract_news", news_queue_selection)
    final_paper_slice = text.index("papers = rank_papers(papers)[: settings.max_papers]", paper_call)
    final_news_slice = text.index("news = rank_news(news)[: settings.max_news]", news_call)
    assert queue_selection < paper_call < final_paper_slice
    assert news_queue_selection < news_call < final_news_slice
    assert "settings.max_papers + max(0, settings.display_candidate_buffer)" in text
    assert "settings.max_news + max(0, settings.display_candidate_buffer)" in text
