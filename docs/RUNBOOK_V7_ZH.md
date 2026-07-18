# v7 运行手册

## 单病毒，不重建词库、不推微信

```bash
gh workflow run daily-intelligence.yml   --repo NailouZhang/pathogen-intelligence-factory   --ref main   -f profile_id=hantavirus   -f refresh_profile=false   -f cover_image_mode=deterministic   -f dispatch_wechat=false   -f review_mode=balanced
```

## 单病毒并推微信

```bash
gh workflow run daily-intelligence.yml   --repo NailouZhang/pathogen-intelligence-factory   --ref main   -f profile_id=hantavirus   -f refresh_profile=false   -f cover_image_mode=auto   -f dispatch_wechat=true   -f review_mode=balanced
```

## 全 21 个 profile 初始化

```bash
gh workflow run daily-intelligence.yml   --repo NailouZhang/pathogen-intelligence-factory   --ref main   -f run_mode=all   -f refresh_profile=true   -f cover_image_mode=deterministic   -f dispatch_wechat=false   -f review_mode=balanced
```

## 观察日志

```bash
RUN_ID="$(gh run list   --repo NailouZhang/pathogen-intelligence-factory   --workflow daily-intelligence.yml   --limit 1   --json databaseId   --jq '.[0].databaseId')"

gh run watch "$RUN_ID"   --repo NailouZhang/pathogen-intelligence-factory
```

日志阶段：`profile`、`query_plan`、`scholarly_retrieval`、`news_retrieval`、`paper_candidate_gate`、`paper_dedup`、`relevance_review`、`display_selection`、`display_content_enrichment`、`deep_analysis`、`translation`、`pipeline`。

## 只重新创建公众号草稿

```bash
SOURCE_SHA="$(gh api   repos/NailouZhang/pathogen-intelligence-factory/commits/intelligence-data   --jq '.sha')"

gh workflow run create-wechat-draft.yml   --repo NailouZhang/pathogen-wechat-publisher   --ref main   -f source_repo=NailouZhang/pathogen-intelligence-factory   -f source_sha="$SOURCE_SHA"   -f package_path=profiles/hantavirus/wechat-package   -f force=true   -f refresh_cover=false
```
