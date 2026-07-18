# 公开仓运行手册 v14

## 1. 调度

工作流每天18:00 UTC触发，并根据Asia/Shanghai实际星期选择当天profile队列，相当于北京时间次日02:00。队列在同一个job中顺序运行，确保免费LLM额度状态可跨profile继承。

## 2. 手动运行单个病原

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  --ref main \
  -f profile_id=marburg_virus \
  -f dispatch_wechat=false \
  -f refresh_profile=false \
  -f cover_image_mode=deterministic \
  -f review_mode=balanced
```

## 3. 按写入顺序运行多个病原

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  --ref main \
  -f profiles='hantavirus,rabies_virus,marburg_virus' \
  -f dispatch_wechat=false \
  -f cover_image_mode=deterministic \
  -f review_mode=balanced
```

## 4. 运行全部21个profile

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  --ref main \
  -f run_mode=all \
  -f dispatch_wechat=false \
  -f cover_image_mode=deterministic \
  -f review_mode=balanced
```

## 5. 本地运行

```bash
cd "$HOME/github-projects/pathogen-intelligence-factory"
bash scripts/run_profile_local.sh rabies_virus /tmp/pif-rabies
```

本地共享provider状态：

```text
$HOME/github-projects/pathogen-intelligence-factory/runtime/shared/provider_quota_daily.json
```

需要清空当天状态重新测试时，先确认没有运行中的profile，然后删除该文件。

## 6. 生产审计顺序

每次运行至少检查：

```bash
jq . /tmp/pif-rabies/data/audit/publication_date_gate.json
jq . /tmp/pif-rabies/data/audit/scholarly_record_type_gate.json
jq . /tmp/pif-rabies/data/audit/event_query_expansion.json
jq . /tmp/pif-rabies/data/audit/news_content_gate.json
jq . /tmp/pif-rabies/data/audit/analysis_quality.json
jq . /tmp/pif-rabies/data/audit/llm_provider_usage.json
jq . /tmp/pif-rabies/data/audit/retrieval_funnel.json
```

## 7. 结果分支

完整SHA：

```bash
gh api repos/NailouZhang/pathogen-intelligence-factory/commits/intelligence-data --jq '.sha'
```

某个profile发布包：

```bash
SHA=$(gh api repos/NailouZhang/pathogen-intelligence-factory/commits/intelligence-data --jq '.sha')
gh api "repos/NailouZhang/pathogen-intelligence-factory/contents/profiles/hantavirus/wechat-package?ref=$SHA" \
  --jq '.[]|[.name,.size,.type]|@tsv'
```

## 8. 失败隔离

- 某个profile失败：记录失败并继续后续profile；
- 私有公众号dispatch失败：公开数据与Pages继续；
- LLM不可用且`PIF_ANALYSIS_REQUIRE_LLM=false`：允许fallback并显示全局质量警告；
- 新闻不足：允许0条，不用无关正文补足数量。
