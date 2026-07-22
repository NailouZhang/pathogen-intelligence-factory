from pifactory.pipeline_v15 import apply_translation_display_fallback


def test_translation_failure_preserves_english_scientific_record():
    item = {
        "title": "Marburg virus disease study",
        "analysis_ready": True,
        "translation_ready": False,
        "analysis": {"analysis": {"background": "English evidence-backed background", "methods": "English methods"}},
        "elements_zh": {"background": ""},
    }

    result = apply_translation_display_fallback(item)

    assert result["translation_incomplete"] is True
    assert result["display_translation_ready"] is True
    assert result["translation_display_fallback"] == "english_source_preserved"
    assert result["title_zh"] == item["title"]
    assert result["elements_zh"]["background"] == "English evidence-backed background"
    assert result["elements_zh"]["methods"] == "English methods"


def test_translation_failure_does_not_rescue_failed_analysis():
    item = {"title": "x", "analysis_ready": False, "translation_ready": False}
    result = apply_translation_display_fallback(item)
    assert result["display_translation_ready"] is False
    assert result["translation_incomplete"] is True
    assert "translation_display_fallback" not in result
