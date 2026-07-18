> 当前工程版本：v14.4。GitHub Actions、本地Conda和CI使用统一Python安装器。

# pathogen-intelligence-factory v14.4

公开的21种病毒每周文献与公共卫生新闻情报工厂。

固定GitHub仓库：`NailouZhang/pathogen-intelligence-factory`
固定本地目录：`$HOME/github-projects/pathogen-intelligence-factory`
固定Conda环境：`$HOME/github-projects/pathogen-intelligence-factory/.conda-env`

## 完整链路

```text
21种病原北京时间顺序调度
→ 多文献源与新闻源检索
→ 真实发表日期硬门禁
→ Dataset/补充材料/仓储对象硬门禁
→ Python全量相关性复核与LLM边界复核
→ 新闻正文身份、主题和错误页熔断
→ L1摘要/L2关键全文证据/L3跨供应商复核
→ elements_en英文结构化要素
→ elements_zh中文翻译镜像
→ 中英文GitHub Pages
→ wechat-package/v2
→ 私有公众号仓repository_dispatch
```

## 已实现的质量策略

- `created_date/indexed_date`只作审计，不决定本周入选；
- Figshare、Zenodo、Dryad、Dataset、Supplement等默认在LLM前拒绝；
- 新闻正文必须自身出现目标病原身份，不允许标题救援无关正文；
- 相同错误URL/正文被多个不同标题复用时熔断；
- 七/五要素严格要求字符串Schema，嵌套字典和列表不能进入HTML；
- Gemini、Groq、OpenRouter、Mistral、SiliconFlow按任务和状态自动轮换；
- 21个profile共享北京时间每日额度/冷却状态；
- 全文只在本地筛选证据，不整体发送给LLM；
- 马尔堡等稀缺病原使用文献事件线索动态增强新闻查询；
- fallback超过阈值时在网页顶部、日志和审计中告警；
- 中英文标题、摘要、要素、总览、统计、来源健康和审计完整切换；
- Top-N是展示限制，不是相关性判定，全部合格记录保存在审计中；
- 渲染完成后再次审计最终HTML，发现字典字面量、英文占位符、数据集卡片或缩写词表污染时阻止发布。

## 核心审计文件

```text
data/audit/publication_date_gate.json
data/audit/scholarly_record_type_gate.json
data/audit/news_content_gate.json
data/audit/paper_post_enrichment_gate.json
data/audit/event_query_expansion.json
data/audit/analysis_quality.json
data/audit/llm_provider_usage.json
data/audit/retrieval_funnel.json
data/audit/display_selection.json
data/audit/eligible_papers.jsonl
data/audit/eligible_news.jsonl
data/audit/rendered_html_quality.json
```

## 本地安装

```bash
cd "$HOME/github-projects/pathogen-intelligence-factory"
bash scripts/bootstrap_dev.sh
"$HOME/github-projects/pathogen-intelligence-factory/.conda-env/bin/python" \
  -m playwright install --with-deps --only-shell chromium
bash scripts/doctor_local.sh
```

## 测试

```bash
cd "$HOME/github-projects/pathogen-intelligence-factory"
"$HOME/github-projects/pathogen-intelligence-factory/.conda-env/bin/python" -m pytest -q
```

## 本地运行

真实模式：

```bash
bash scripts/run_profile_local.sh hantavirus /tmp/pif-hantavirus
```

离线演示：

```bash
bash scripts/run_profile_local.sh hantavirus /tmp/pif-hantavirus-demo --demo
```

## GitHub运行

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  --ref main \
  -f profile_id=hantavirus \
  -f dispatch_wechat=false \
  -f cover_image_mode=deterministic \
  -f review_mode=balanced
```

完整安装、Secrets、Variables、Pages、Runner和公众号操作见：

```text
docs/INSTALL_ZH.md
docs/OPERATIONS_V14_ZH.md
docs/QUALITY_AND_BILINGUAL_REPAIRS_V14_ZH.md
docs/CURRENT_ENGINEERING_LIMITATIONS_V14_ZH.md
REPAIR_LEDGER_ZH.md
```
