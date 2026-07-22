from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_prompt_enforces_exact_sources_and_scope_boundary():
    text = (ROOT / "prompts/profile_bootstrap_v3.md").read_text(encoding="utf-8")
    for phrase in (
        "不得调用搜索引擎",
        "不得使用模型记忆补齐",
        "每一个顶层 OR 分支",
        "related_entity_terms",
        "hard_exclusion_terms",
        "context_terms",
        "branch_anchor_check",
        "只输出一个合法 JSON 对象",
    ):
        assert phrase in text
