from __future__ import annotations

from pathlib import Path

from pifactory.bundled_vocabulary import load_bundled_vocabulary
from pifactory.query_plan import build_relevance_rules
from pifactory.relevance_guard import _relaxed_accept, apply_relevance_cliff_guard

ROOT = Path(__file__).resolve().parents[1]


def _profile() -> dict:
    profile = load_bundled_vocabulary(ROOT, "hantavirus")["profile"]
    profile["post_retrieval_relevance_rules"] = build_relevance_rules(profile)
    return profile


def test_field_specific_thresholds_and_second_level_relaxation() -> None:
    profile = _profile()
    title_record = {"paper_id": "title", "title": "Hantavirus surveillance report"}
    ok_title, audit_title = _relaxed_accept(title_record, profile, "paper", 1)
    assert ok_title is True
    assert "title" in audit_title["accepted_fields"]

    weak_excluded = {
        "paper_id": "body",
        "title": "Reservoir surveillance",
        "abstract": "A fish hantavirus was described.",
    }
    ok_level_1, _ = _relaxed_accept(weak_excluded, profile, "paper", 1)
    ok_level_2, audit_level_2 = _relaxed_accept(weak_excluded, profile, "paper", 2)
    assert ok_level_1 is False
    assert ok_level_2 is False
    assert audit_level_2["level"] == 2
    assert audit_level_2["accepted_fields"] == []


def test_hard_identity_conflicts_are_never_relaxed() -> None:
    record = {"paper_id": "wrong", "title": "Hantavirus report", "identifier_conflict": True}
    for level in (1, 2, 3):
        accepted, audit = _relaxed_accept(record, _profile(), "paper", level)
        assert accepted is False
        assert audit["hard_conflict"] is True


def test_cliff_guard_runs_level_two_only_when_level_one_is_insufficient(monkeypatch) -> None:
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_MIN_CANDIDATES", "10")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_MIN_ACCEPTED", "5")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_PREVIOUS_RATIO", "0.50")
    profile = _profile()
    candidates = [
        {"paper_id": f"p{i}", "title": "Reservoir surveillance", "abstract": "Hantavirus infection was investigated in rodents with epidemiological surveillance."}
        for i in range(12)
    ]
    output, audit = apply_relevance_cliff_guard(candidates, [], profile, kind="paper", previous_accepted=12)
    assert audit["triggered"] is True
    assert audit["levels"]
    assert len(output) >= 5
    assert audit["field_thresholds"]["abstract"] == [5, 4, 3, 2]
