from pathlib import Path


def test_workflow_streams_python_output_and_sets_v15_budgets():
    text = Path(".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    assert "python -u scripts/run_daily.py" in text
    assert '| tee "/tmp/${PROFILE_ID}.combined.log"' in text
    assert 'PIF_MAX_NEWS_FETCHES: "80"' in text
    assert "vars.PIF_MAX_FULLTEXTS || '150'" in text
    assert "vars.PIF_DISPLAY_CANDIDATE_BUFFER || '100'" in text
    assert "vars.PIF_MAX_SUPPLEMENTARY_PAPERS || '100'" in text
    assert "vars.PIF_FULLTEXT_BATCH_SIZE || '25'" in text
    assert "timeout --signal=TERM" in text


def test_pipeline_completes_after_dedup_and_replenishes_primary_report():
    text = Path("src/pifactory/pipeline_v15.py").read_text(encoding="utf-8")
    dedup = text.index("dedup_papers")
    lifecycle = text.index('progress(\n        "literature_lifecycle", "start"', dedup)
    completion = text.index("complete_literature_catalog(", lifecycle)
    final_review = text.index("_review_paper_batch(completed", completion)
    analysis = text.index("_analyze_translate_paper(item)", final_review)
    comparison_pool = text.index("comparison_pool = rank_papers", final_review)
    selection = text.index("select_primary_and_supplementary(", comparison_pool)
    assert dedup < lifecycle < completion < final_review < comparison_pool < analysis < selection
    assert "max_budget=len(current)" in text
    assert "completion_processed < settings.max_fulltexts" in text
    assert "len(primary_ready) >= settings.max_papers" in text
    assert "analysis_attempt_budget_exhausted" in text
    assert "supplementary_limit=settings.max_supplementary_papers" in text
