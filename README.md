# pathogen-intelligence-factory v10

面向 21 个病毒主题的每周文献与公共卫生新闻情报系统。公开仓负责五核心概念检索、多数据库元数据汇总、全候选相关性复核、跨来源去重、时效与质量排序、展示候选内容补全、逐篇双语精读、文献/新闻分离汇总、GitHub Pages 以及 `wechat-package/v2`。

## v10 后处理修复

- “本期文献进展”不再按输入顺序取前 N 条，而是在 15～25 篇范围内优先选择报告时间窗内发表、证据和质量更高、来源与主题更有代表性的文献。
- 检索、去重、复核、最终纳入等统计单独显示为醒目的概览行。
- 汇总输出过滤模型内部保留句、翻译占位符、省略号和未完成句，并校验真实 `paper_id` / `news_id`。
- 原始研究七要素、综述五要素和新闻五要素绑定独立修辞角色；LLM 输出后再次进行跨字段句子与语义去重、证据角色补位和完整句修复。
- 新闻链路按 RSS 元数据、真实 URL 清洗、静态多提取器、标准 Playwright Chromium 渲染、实质性来源摘要依次降级；不使用 stealth、验证码处理、登录自动化、代理轮换或访问控制绕过。
- 新闻正文成功后逐篇提取五要素，并生成不超过 500 个中文字符的公众号简报。
- 页面和公众号继续使用“每周情报”标题。

## 本地验证

```bash
python -m pip install -r requirements.txt
python -m playwright install --with-deps --only-shell chromium
python scripts/validate_all_profiles.py
python scripts/audit_query_coverage.py --output /tmp/query-coverage-v10.json
python -m pytest -q
python -m compileall -q src scripts tests
python scripts/run_daily.py --profile hantavirus --demo --output-dir /tmp/pif-v10-demo
python scripts/validate_wechat_package.py /tmp/pif-v10-demo/wechat-package
```

完整安装、GitHub Actions、Secrets、Pages、Runner 和公众号草稿命令见 `docs/INSTALL_ZH.md` 与完整双仓包根目录文档。
