from pathlib import Path
from types import SimpleNamespace
import json

import yaml

from src.pifactory.profile_contract import deterministic_profile
from src.pifactory.query_plan import compile_profile_queries
from src.pifactory.relevance import build_compact_evidence_packet, final_filter, pack_by_token_budget

ROOT = Path(__file__).resolve().parents[1]


class RecordingLLM:
    available = True

    def __init__(self):
        self.calls = []

    def json_task(self, **kwargs):
        payload = json.loads(kwargs["prompt"])
        rows = payload["records"]
        self.calls.append([row["id"] for row in rows])
        return SimpleNamespace(data={"d": [{"id": row["id"], "c": "A", "p": 95, "r": "target"} for row in rows]})


def _profile():
    seed = yaml.safe_load((ROOT / "profiles" / "hantavirus" / "seed.yaml").read_text(encoding="utf-8"))
    docs = [{"url": x["url"], "usable": True, "sha256": "test"} for x in seed["authoritative_sources"]]
    return compile_profile_queries(deterministic_profile(seed, docs))


def test_all_candidates_are_reviewed_without_document_count_cutoff():
    profile = _profile()
    rows = [
        {
            "title": f"Occupational exposure study {i}",
            "abstract": "A long introduction without the identity. Hantavirus infection surveillance was performed in workers exposed to rodent reservoirs. Laboratory testing and follow-up were reported.",
            "retrieval_queries": ["Hantavirus"],
        }
        for i in range(37)
    ]
    llm = RecordingLLM()
    accepted = final_filter(rows, profile, llm, kind="paper", compact_batch_tokens=1800)
    assert len(accepted) == 37
    reviewed = [rid for call in llm.calls for rid in call]
    assert len(reviewed) == 37
    assert len(set(reviewed)) == 37
    assert len(llm.calls) > 1, "small token budget should create multiple dynamic batches"
    assert not any(x.get("relevance_review_budget_exceeded") for x in rows)


def test_compact_packet_selects_identity_evidence_not_prefix_truncation():
    profile = _profile()
    record = {
        "title": "Occupational exposure study",
        "abstract": "Background material without a virus name. Another general sentence. Puumala virus antibodies were detected in forestry workers. Rodent exposure was associated with seropositivity.",
        "retrieval_queries": ["Puumala virus"],
    }
    packet = build_compact_evidence_packet(record, profile, "paper", "r1")
    assert any("Puumala virus" in text for text in packet["ev"])


def test_token_packer_never_drops_items():
    items = [({"n": i}, {"id": str(i), "ev": ["x" * 300]}) for i in range(23)]
    batches = pack_by_token_budget(items, token_budget=1500, fixed_prompt_tokens=500)
    assert sum(len(batch) for batch in batches) == len(items)
