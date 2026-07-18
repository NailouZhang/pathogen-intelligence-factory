# pathogen-intelligence-factory v9

面向 21 个病毒主题的每周文献与公共卫生新闻情报系统。公开仓负责：五核心概念检索、多数据库元数据汇总、全候选相关性复核、去重、展示候选内容补全、逐篇双语分析、文献/新闻分离汇总、GitHub Pages 和 `wechat-package/v2`。

## v9 修复重点

- 翻译失败不再把 50 篇候选直接压缩为更少条目：展示候选池保持到翻译结束，最终从所有翻译就绪记录中重新排序并补足 Top 50。
- 新闻不再只依赖聚合器页面：保留 RSS 中的原站候选链接、合并 GDELT 等直接发布者 URL、解析 canonical/JSON-LD/脚本链接，并允许通过质量门禁的实质性转载摘要作为降级证据。
- 文献汇总和新闻汇总完全分开；中文汇总必须通过中文比例、完整句、无省略号和来源 ID 校验。
- 研究七要素与综述五要素加入修辞角色约束。背景、设计、方法、结果、解释、意义和局限分别绑定对应证据句。
- 封面和页面标题统一为“每周情报”。

## 本地验证

```bash
python -m pip install -r requirements.txt
python scripts/validate_all_profiles.py
python scripts/audit_query_coverage.py --output /tmp/query-coverage-v9.json
python -m pytest -q
python -m compileall -q src scripts tests
python scripts/run_daily.py --profile hantavirus --demo --output-dir /tmp/pif-v9-demo
python scripts/validate_wechat_package.py /tmp/pif-v9-demo/wechat-package
```

## 日常手工运行

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

完整安装、升级、Secrets、Pages、Runner 和公众号草稿命令见 `docs/INSTALL_ZH.md` 与完整双仓包根目录文档。
