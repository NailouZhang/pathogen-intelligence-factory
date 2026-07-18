from pifactory.source_status import SourceAudit


def test_zero_results_are_distinct_from_failure_and_skip():
    audit = SourceAudit()
    audit.add(source="PubMed", query="q1", status="success", records=0)
    audit.add(source="PubMed", query="q2", status="success", records=7)
    audit.add(source="OpenAlex", status="skipped", error="missing key")
    audit.add(source="Crossref", query="q", status="failed", error="timeout")
    summary = {x["source"]: x for x in audit.summary()["sources"]}
    assert summary["PubMed"]["successful_queries"] == 2
    assert summary["PubMed"]["zero_result_queries"] == 1
    assert summary["OpenAlex"]["skipped_queries"] == 1
    assert summary["Crossref"]["failed_queries"] == 1
    assert summary["PubMed"]["health"] == "healthy"
    assert summary["OpenAlex"]["health"] == "skipped"
    assert summary["Crossref"]["health"] == "failed"
