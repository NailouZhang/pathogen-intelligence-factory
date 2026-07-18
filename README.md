# pathogen-intelligence-factory v8

公开仓每天按北京时间 02:00 顺序处理 3 个病原 profile，检索过去 7 天文献与新闻，完成相关性复核、去重、排序、最终展示内容补全、类型化单篇分析、免费翻译链、分离式文献/新闻汇总、GitHub Pages 和 `wechat-package/v2`。

## v8 重点

- 每个病毒约 5 个核心检索概念，避免重复和长查询。
- 只有 Top 50 与最多 20 条补位候选进行全文/新闻正文获取。
- 文献汇总和新闻汇总完全分离，各使用 15～25 条。
- 原始研究七要素、综述五要素、新闻五要素。
- 新闻必须获得正文；RSS 摘要或标题不能冒充正文。
- 微信单条新闻中文摘要不超过 500 字符。
- 翻译顺序为免费 Python 翻译器，Gemini/Groq 最终兜底；不使用 Google Cloud Translation。
- 翻译失败记录不以占位符进入最终页面。

## 快速验证

```bash
python -m pip install -r requirements.txt
python scripts/validate_all_profiles.py
python scripts/audit_query_coverage.py
python -m pytest -q
python scripts/run_daily.py --profile hantavirus --demo --output-dir /tmp/pif-v8-demo
```

## 工作流手工运行

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  --ref main \
  -f profile_id=hantavirus \
  -f refresh_profile=false \
  -f cover_image_mode=deterministic \
  -f dispatch_wechat=false \
  -f review_mode=balanced
```

## 计划

| 星期（北京时间） | profile 1 | profile 2 | profile 3 |
|---|---|---|---|
| 周一 | seasonal_influenza | sars_cov_2 | respiratory_syncytial_virus |
| 周二 | human_metapneumovirus | human_adenovirus | human_enterovirus |
| 周三 | norovirus | measles_virus | human_papillomavirus |
| 周四 | dengue_virus | chikungunya_virus | avian_influenza |
| 周五 | hantavirus | sftsv | mpox_virus |
| 周六 | nipah_virus | arenaviridae | ebola_viruses |
| 周日 | marburg_virus | rabies_virus | hepatitis_b_virus |

详细文档位于 `docs/`。完整双仓部署命令位于交付包根目录。
