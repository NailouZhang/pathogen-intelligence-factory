from pathlib import Path

from pifactory.analysis import build_paper_evidence
from pifactory.content import resolve_and_extract_news
from pifactory.dedup import dedup_news
from pifactory.overview import _overview_validator


class BrokenHTTP:
    def request(self, *args, **kwargs):
        raise RuntimeError("network unavailable")


def test_substantive_rss_summary_is_retained_with_clear_provenance():
    summary = (
        "The regional health department reported a laboratory-confirmed hantavirus infection in a rural resident. "
        "The patient was hospitalized and an exposure investigation focused on rodent-contaminated buildings. "
        "Officials advised residents to ventilate enclosed spaces and avoid sweeping dry rodent droppings."
    )
    record = resolve_and_extract_news(
        BrokenHTTP(),
        {
            "news_id": "n1",
            "title": "Health department reports hantavirus infection",
            "url": "https://news.google.com/example",
            "excerpt": summary,
        },
    )
    assert record["content_status"] == "syndicated_summary"
    assert record["content"].startswith("The regional health department")
    assert len(record["content"]) >= 280
    assert record["content_method"] == "rss_syndicated_summary"
    assert record["content_audit"]["provenance"] == "syndicated_summary"


def test_title_only_rss_item_is_still_rejected():
    record = resolve_and_extract_news(
        BrokenHTTP(),
        {
            "news_id": "n2",
            "title": "Hantavirus report",
            "url": "https://news.google.com/example2",
            "excerpt": "Hantavirus report",
        },
    )
    assert record["content_status"] == "title_only_rejected"
    assert not record["content"]


def test_news_dedup_preserves_direct_publisher_url_from_other_provider():
    records = [
        {
            "source": "Google News English",
            "title": "Health authority confirms hantavirus case",
            "url": "https://news.google.com/rss/articles/abc",
            "published_date": "2026-07-18",
            "excerpt": "A health authority confirmed a case and began an exposure investigation.",
        },
        {
            "source": "GDELT DOC 2.0",
            "title": "Health authority confirms hantavirus case",
            "url": "https://publisher.example.org/health/hantavirus-case",
            "published_date": "2026-07-18",
            "excerpt": "",
        },
    ]
    merged = dedup_news(records)
    assert len(merged) == 1
    assert merged[0]["url"] == "https://publisher.example.org/health/hantavirus-case"
    assert "https://publisher.example.org/health/hantavirus-case" in merged[0]["candidate_urls"]


def test_structured_abstract_sentences_receive_rhetorical_roles():
    work = {
        "paper_id": "p1",
        "title": "Trial of immune plasma",
        "abstract": (
            "BACKGROUND: Hantavirus cardiopulmonary syndrome has high mortality. "
            "METHODS: We conducted a non-randomized multicentre trial in 32 patients. "
            "RESULTS: Four of 29 confirmed patients died, corresponding to 14%. "
            "CONCLUSIONS: Immune plasma was feasible, but randomized evaluation is needed."
        ),
    }
    evidence = build_paper_evidence(work)["evidence"]
    roles = {row["role"] for row in evidence}
    assert {"background", "methods", "results", "conclusion"}.issubset(roles)


def test_overview_validator_rejects_english_chinese_fields_and_ellipsis():
    validator = _overview_validator({"p1", "p2", "p3"}, "literature")
    base = {
        "headline_zh": "本期汉坦病毒文献研究取得多项进展",
        "lead_zh": "本期研究围绕流行病学、临床干预与宿主生态展开，多篇文献提供了可相互印证的证据。",
        "key_findings_zh": [
            "多中心研究报告了明确的临床结局，并保留了样本量和效应指标。[p1]",
            "流行病学调查提示职业暴露仍是近期研究重点。[p2]",
            "综述强调监测标准化和前瞻性研究仍存在缺口。[p3]",
        ],
        "trend_or_risk_zh": "研究热点正在从单点描述扩展到临床、生态和分子监测的综合评估。",
        "caveats_zh": "部分结论仅基于摘要或观察性研究，不能直接推断因果关系。",
        "headline_en": "Recent hantavirus literature",
        "lead_en": "This weekly brief prioritizes recent evidence and distinguishes study results from interpretation across eligible publications.",
        "key_findings_en": [
            "A recent clinical study reported a directly observed result with its study design and sample context. [p1]",
            "Epidemiological evidence identified a current surveillance or exposure pattern. [p2]",
            "Review evidence identified concrete methodological gaps and future priorities. [p3]",
        ],
        "trend_or_risk_en": "Current research increasingly combines clinical, epidemiological, ecological and molecular evidence rather than relying on isolated descriptions.",
        "caveats_en": "Some evidence is observational or abstract-only, so associations should not be interpreted as established causal effects.",
        "brief_en": "This briefing integrates primary studies and reviews across clinical, epidemiological, ecological, and genomic themes. It preserves study design and quantitative evidence while distinguishing direct results from interpretation and identifying limitations in abstract-only and observational evidence.",
        "source_ids": ["p1", "p2", "p3"],
    }
    ok, _ = validator(base)
    assert ok
    bad_english = dict(base, lead_zh="This field is English and should not pass the Chinese validator.")
    assert validator(bad_english)[0] is False
    bad_ellipsis = dict(base, lead_zh=base["lead_zh"] + "……")
    assert validator(bad_ellipsis)[0] is False


def test_cover_and_issue_titles_are_weekly():
    cover = Path("src/pifactory/cover.py").read_text(encoding="utf-8")
    pipeline = Path("src/pifactory/pipeline_v15.py").read_text(encoding="utf-8")
    assert "全球病原每周情报" in cover
    assert "每周情报" in pipeline
    assert "Weekly Intelligence" in pipeline


def test_primary_top_n_and_supplementary_are_selected_after_analysis_translation():
    pipeline = Path("src/pifactory/pipeline_v15.py").read_text(encoding="utf-8")
    analysis = pipeline.index("def _analyze_translate_paper")
    primary_ready = pipeline.index("primary_ready.append(item)", analysis)
    replenishment = pipeline.index('"primary_report_replenishment", "batch_complete"', primary_ready)
    supplementary_titles = pipeline.index("supplementary_title_candidates", replenishment)
    selection = pipeline.index("select_primary_and_supplementary(", supplementary_titles)
    assert analysis < primary_ready < replenishment < supplementary_titles < selection
    assert '"supplementary_papers": supplementary_papers' in pipeline
