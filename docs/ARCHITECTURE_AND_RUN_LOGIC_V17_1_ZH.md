# v17.1 三仓架构与完整运行逻辑

## 架构边界

私有 Factory 仓是唯一生产代码源，负责21种病毒调度、数据库召回、跨库去重、正文获取、新闻正文诊断、相关性终审、双语分析、HTML/微信包生成、后台审计和数据分支提交。公开 Pages 仓只接收经过白名单过滤的静态 `_site`。私有 Publisher 仓不重新分析内容，只验证 Factory 数据分支中的不可变发布包，并通过本地 Runner 调用微信公众号 API。

数据流为：

```text
定时/手动触发私有Factory
→ 加载内置21种病毒Profile与词库
→ 文献和新闻召回
→ 日期门禁与跨库去重
→ 新闻多提取器正文提取与污染诊断
→ 标题/摘要或简讯/正文分字段相关性终审
→ 一级断崖保护
→ 仍异常时二级放宽排除标准
→ LLM双语分析与规则兜底
→ display_issue公开净化
→ Pages静态站与微信发布包分别渲染
→ 完整数据和审计写入intelligence-data
→ 白名单静态站推送公开Pages仓
→ 公开仓独立部署GitHub Pages
→ repository_dispatch触发私有Publisher
→ 本地Runner使用SOURCE_REPO_TOKEN下载指定SHA发布包
→ 校验字符预算、来源、Schema、哈希与重复状态
→ 写入微信公众号草稿箱
```

## 21套内置词库

所有21个 Profile，包括 SARS-CoV-2，都由本版本重新生成，不沿用旧运行时生成结果。每个 Profile 包含 `manifest.json`、`profile.json`、`retrieval_vocabulary.json`、`review_vocabulary.json`、`exclusion_vocabulary.json`、`translation_glossary.json`、`authoritative_sources.json` 和 `validation_cases.json`。检索词负责召回，终审词负责判定，两者不能互相替代。

生产默认值为：

```text
PIF_VOCAB_SOURCE=bundled
PIF_VOCAB_ALLOW_RUNTIME_REFRESH=false
PIF_REVIEW_ALLOW_CORE_TERMS_FALLBACK=false
```

运行时数据分支中的旧词库只能作为审计历史，不能覆盖版本更高且通过验证的内置词库。人工维护模式可以显式打开运行时刷新，但不属于每日生产路径。

## 分字段终审与三级输出连续性降级

标题、摘要或新闻简讯、全文正文使用不同接受阈值。标题要求精确身份锚点；摘要/简讯允许上下文支持，但需要更高综合证据；正文可以通过多段实体和主题证据累计。文献和新闻也分别配置。

默认四档阈值（标准、一级、二级、三级）为：

```text
paper_title       4 → 3 → 2 → 1
paper_abstract    5 → 4 → 3 → 2
paper_full_body   4 → 3 → 2 → 1
news_title        5 → 4 → 3 → 2
news_brief        6 → 5 → 4 → 3
news_full_body    4 → 3 → 2 → 1
```

标准终审使用完整内置词库和标准排除规则。满足以下任一条件即触发恢复：候选不少于100且接受少于10；候选不少于100且接受比例低于15%；本次接受数低于上一有效结果的20%。一级降低软字段阈值；二级允许多字段身份与上下文联合并放宽软排除；三级只恢复具有明确目标病毒身份且元数据或新闻来源可靠的记录。错误病毒、标识符冲突、正文身份冲突和模型明确错误实体等硬冲突永不放宽。

若三级后仍低于目标，流程不会因数量不足中断：有合格记录时生成 `qualified_low_volume_output`，完全没有安全记录时生成 `empty_valid_issue`。系统不会为了达到固定数量而伪造或强行接受无身份记录。

## 多语言分析契约

原始标题、摘要和正文保存来源语言标签。英文结构化字段在 LLM 成功、LLM失败和旧缓存命中时均执行语言净化；日文、中文、韩文和西里尔文原句保留在后台原文证据区，不再写入英文要素。渲染器再次校验英文 `<dd>`，工作流在首次审计失败时执行确定性重渲染并进行第二次严格审计。

## 新闻正文提取与诊断

新闻正文不再直接使用整页 `get_text()`。处理顺序为 Trafilatura、Readability、文章语义 DOM、BeautifulSoup 兜底；在正文提取前删除导航、页眉、页脚、侧栏、Cookie、会员、应用推广、推荐阅读、栏目矩阵和广告节点。静态提取失败后才使用有上限的 Playwright 渲染，不处理验证码、登录墙或访问绕过。

正文和来源简讯分别诊断。主要指标包括正文字符数、段落数、导航词占比、重复行占比、句子占比、短行占比和硬噪声短语。污染正文先切换第二提取器；仍失败时只保留可信标题、来源、日期和摘要，并降为 `metadata_only`，不得进入主新闻分析。

## 公开内容净化

完整 `issue` 包含内部策略、翻译状态、字符预算过程、终审生命周期和审计字段。Pages 与微信渲染前统一生成 `display_issue`，删除后台字段和运营提示。字符压缩、翻译回退、Top50规则、证据边界和省略数量仍写入审计文件，但不再出现在公开页面。

顶部只展示两行12px灰色统计。“本期文献进展”和“本期新闻动态”由模型输出结构化3至5条项目，公开页面按项目符号展示，不再渲染长段落。

## Pages公开边界

Factory 只同步：

```text
index.html
profiles/*/index.html
assets/*
images/*
portal.json
robots.txt
.nojekyll
```

公开仓验证工作流拒绝 `.py`、词库、密钥、审计目录、配置目录和生产脚本。公开 HTML、图片和用于页面显示的 JSON 仍属于公开内容，可被浏览器保存；生产代码、提示词、完整词库和内部审计不再公开。

## 故障隔离

某个 Profile 失败不会删除其他 Profile 的历史数据。Pages 同步采用成功后推送，公开仓部署失败不回滚 Factory 数据。Publisher dispatch 是后置步骤，微信失败不影响 Pages 和情报数据。Publisher 只读取完整40位 SHA，对同一 `publish_key` 保持重复保护，`force=true` 时才允许重复草稿。
