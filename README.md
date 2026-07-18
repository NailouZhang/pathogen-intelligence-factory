# pathogen-intelligence-factory v13

公开的21病毒每周文献与公共卫生新闻情报工厂。固定GitHub仓库为 `NailouZhang/pathogen-intelligence-factory`，本地位置为 `$HOME/github-projects/pathogen-intelligence-factory`。

## 主要功能

- 按北京时间每周轮转21种病毒；
- PubMed、Europe PMC、Crossref、OpenAlex、Semantic Scholar、bioRxiv、medRxiv等文献源；
- Google/Bing RSS、GDELT、WHO、ReliefWeb等新闻源；
- 真实发表日期硬门禁，索引日期只做审计；
- 文献和新闻双阶段相关性复核；
- 新闻URL身份、正文主题和重复错误页熔断；
- Gemini、Groq、OpenRouter、Mistral、SiliconFlow自动轮换；
- L1摘要、L2全文关键证据、L3跨供应商复核；
- 中文默认页面和完整英文切换；
- 结构化七要素/五要素及全局fallback告警；
- GitHub Pages、审计数据和 `wechat-package/v2`；
- 通过不可变数据提交SHA触发私有微信公众号草稿仓。

## 统计口径

网页明确区分：

```text
原始记录
→ 时间窗
→ 候选门禁
→ 去重
→ 相关性复核通过
→ 正文/分析/翻译门禁后可展示
→ 按优先级、证据强度、时效性和来源质量排序
→ PIF_MAX_PAPERS/PIF_MAX_NEWS Top-N
→ 最终展示
```

Top-N之外的合格记录写入：

```text
data/audit/display_selection.json
data/audit/eligible_papers.jsonl
data/audit/eligible_news.jsonl
```

## 本地安装和测试

```bash
cd "$HOME/github-projects/pathogen-intelligence-factory"
bash scripts/bootstrap_dev.sh
"$HOME/github-projects/pathogen-intelligence-factory/.conda-env/bin/python" -m playwright install --with-deps --only-shell chromium
bash scripts/doctor_local.sh
```

## 本地演示

```bash
bash scripts/run_profile_local.sh hantavirus /tmp/pif-hantavirus-demo --demo
"$HOME/github-projects/pathogen-intelligence-factory/.conda-env/bin/python" scripts/issue_summary.py /tmp/pif-hantavirus-demo/data/latest.json
```

完整安装和运行见：

```text
docs/OPERATIONS_V13_ZH.md
docs/STATISTICS_AND_SELECTION_V13_ZH.md
docs/INSTALL_ZH.md
```

修复账本：`REPAIR_LEDGER_ZH.md`。
