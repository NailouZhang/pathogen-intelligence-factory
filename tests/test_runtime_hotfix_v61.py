from pathlib import Path


def test_workflow_streams_python_output_and_bounds_display_enrichment():
    text = Path(".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    assert "python -u scripts/run_daily.py" in text
    assert '| tee "/tmp/${PROFILE_ID}.combined.log"' in text
    # v9 keeps a bounded Top 50 plus 30 replacement candidates through
    # translation, then selects the final 50 only after the translation gate.
    assert 'PIF_MAX_NEWS_FETCHES: "80"' in text
    assert 'PIF_MAX_FULLTEXTS: "80"' in text
    assert 'PIF_DISPLAY_CANDIDATE_BUFFER: "30"' in text
    assert 'PIF_SEMANTIC_ANONYMOUS_QUERY_LIMIT: "5"' in text
    assert "timeout --signal=TERM" in text


def test_pipeline_keeps_bounded_replacement_pool_until_translation_gate():
    text = Path("src/pifactory/pipeline.py").read_text(encoding="utf-8")
    queue_selection = text.index("paper_queue = rank_papers(papers)")
    news_queue_selection = text.index("news_queue = rank_news(news)")
    paper_call = text.index("lambda item: enrich_scholarly_work", queue_selection)
    news_call = text.index("lambda item: resolve_and_extract_news", news_queue_selection)
    analysis = text.index('progress("deep_analysis", "start"', paper_call)
    translation_gate = text.index('"translation_gate", "complete"', analysis)
    final_paper_slice = text.index("papers = ranked_paper_ready_pool[: settings.max_papers]", analysis)
    final_news_slice = text.index("news = ranked_news_ready_pool[: settings.max_news]", analysis)
    assert queue_selection < paper_call < analysis < final_paper_slice < translation_gate
    assert news_queue_selection < news_call < analysis < final_news_slice < translation_gate
    assert "settings.max_papers + max(0, settings.display_candidate_buffer)" in text
    assert "settings.max_news + max(0, settings.display_candidate_buffer)" in text
