# 病原每周情报工程修复账本

本文件是后续逐项修复的唯一持续性约束记录。每完成一个问题，必须追加：问题、根因、不可回退规则、改动文件、审计输出和回归测试。后续修复不得破坏已完成项目。

## 修复项 01：旧文献被当作“本周新文献”收录

### 状态

已设计并实现补丁；核心回归测试通过。

### 根因

1. `online_date / first_publication_date / published_date / print_date` 与 `created_date / indexed_date` 被放入同一“可用日期”优先级列表。
2. PubMed 查询使用 `CRDT/EDAT`，Europe PMC 查询使用 `CREATION_DATE`，Crossref 默认开启 `from-index-date`。
3. 只要索引日期落入本周，旧论文即可通过窗口门禁。
4. 原有 7 天窗口按起止日期双端包含，实际覆盖 8 个自然日。
5. 缺失月或日的日期被静默补成 1 月 1 日，掩盖日期精度。

### 不可回退规则

1. 真实发表日期字段与元数据日期字段必须永久分离：
   - 真实发表日期：`online_date`、`first_publication_date`、`published_date`、`print_date`。
   - 元数据日期：`created_date`、`indexed_date`。
2. `created_date/indexed_date` 只用于审计，任何情况下都不能单独使文献通过日期门禁。
3. 检索端优先使用数据库原生“发表日期”过滤器；索引日期通道默认关闭。
4. 入库端必须再次执行真实发表日期硬门禁，不能信任检索端已完成过滤。
5. 任一可信真实发表日期明确早于窗口起点时，即使索引日期在本周，也必须拒收。
6. 没有真实发表日期、只有元数据日期的记录不得进入公开网页，只能进入审计隔离区。
7. 年份精度不足以证明“本周发表”；仅年份记录不得进入周报。
8. 允许期刊未来排期，但必须设置有限未来宽限期，默认 90 天；超过宽限期拒收。
9. 7 天窗口必须是包含结束日的连续 7 个自然日，即 `end - 6 days` 至 `end`。
10. 网页主卡片不得再把数据库创建/索引日期显示为“Report date”。

### 新增审计

`data/audit/publication_date_gate.json`

记录：

- 报告窗口；
- 未来宽限期与检索截止日；
- 接收数量及状态；
- 拒绝数量及原因；
- 每条被拒记录的真实发表日期、元数据日期和门禁决策。

### 主要改动文件

- `src/pifactory/dates.py`
- `src/pifactory/scholarly.py`
- `src/pifactory/config.py`
- `src/pifactory/pipeline.py`
- `src/pifactory/render.py`
- `src/pifactory/analysis.py`
- `src/pifactory/overview.py`
- `.github/workflows/daily-intelligence.yml`
- `tests/test_publication_date_gate_v11.py`
- `tests/test_live_adapter_contracts_v5.py`

### 回归测试必须覆盖

- 旧真实发表日期 + 本周 indexed/created 日期：拒绝。
- 只有 indexed/created 日期：拒绝。
- 本周 online 日期 + 未来 print 日期：接收，以 online 为规范日期。
- 只有未来 print 日期且在宽限期内：接收并标记 `future_scheduled`。
- 未来日期超过宽限期：拒绝。
- 仅年份日期：拒绝。
- PubMed 查询不得包含 `CRDT/EDAT`。
- Europe PMC 查询不得包含 `CREATION_DATE`。
- Crossref 默认不得启用 `from-index-date`。
- 7 天窗口必须恰好覆盖 7 个自然日。

## 修复项 02：新闻正文抓到导航栏、W3C 规范页或网站侧栏

### 状态

已在修复项 01 的工程基础上实现；专项和全量回归测试通过。

### 根因

1. `_external_news_urls()` 在所有落地页中收集全部 `<a href>` 和脚本内 HTTP URL，普通媒体页面的侧栏、规范文档、社交链接和全站热门文章都可能进入候选队列。
2. `_news_text_quality()` 只检查长度、句子数、词汇多样性和少量 cookie/subscribe 噪声，未验证正文是否真正包含目标病原体或疾病身份。
3. RSS 标题已通过初次相关性审核后，可以掩盖抓取正文与标题完全不相关的问题。
4. `relevance_post_enrichment` 虽然被计算，但只作审计，`reject` 记录仍继续进入翻译、精读、总览和网页。
5. 并行抓取结束后没有按 `resolved_url/content_hash` 检查多个不同标题是否复用了同一错误页。
6. Playwright 对 401/403/429/451/5xx 响应仅依赖正文提示词识别，部分拦截页仍可能被当作成功页面。
7. Google/Bing RSS 主体实际由 `feedparser` 解析；BeautifulSoup 仅处理条目中的 HTML 摘要片段。问题不是整个 RSS 被 HTML 解析，而是后续落地页外链发现和正文门禁失效。

### 不可回退规则

1. 普通媒体页面不得遍历所有外链或脚本 URL；只允许同站 canonical、Open Graph、Twitter、citation 和 JSON-LD 主体 URL。
2. 只有 Google News/Bing 等已知聚合页可以解析外部候选 URL；候选必须匹配预期 publisher 域名或与原始标题具有足够相似度。
3. W3C、Schema.org、XML.org 等标准/文档站点必须在请求前阻断，不能成为新闻正文来源。
4. 每个正文抽取候选必须执行“正文自身病原身份门禁”：调用 `relevance_assessment("", body, profile)`，标题不得参与正文身份判定。
5. 正文至少必须命中 profile 中的病原身份、成员病毒、疾病名或合格缩写；完全不含身份词时无条件拒绝并继续尝试下一个 URL。
6. 只有一个侧栏标题命中病原词、页面标题又与原始新闻不匹配时，不得判定为正文；重复同一侧栏文本不能通过增加出现次数升级为强证据。
7. 有效 RSS 实质摘要可以兜底，但摘要本身也必须通过正文身份门禁；错误落地页 URL不得覆盖该摘要的原始 RSS URL。
8. `relevance_post_enrichment` 必须作为硬过滤器实际生效：新闻按正文独立复核；论文按标题与摘要/全文复核；`reject` 记录必须在翻译和精读前删除。
9. 同一次运行中，同一 `resolved_url` 或 `content_hash` 被多个标题复用时必须熔断：
   - 标题明显不同：整组作为共享错误页拒绝；
   - 标题高度相似：仅保留质量最高的一条，其他作为抓取后重复记录删除。
10. Playwright 返回 401、403、407、408、409、429、451 或 5xx 时必须判定为 blocked，不得将页面 HTML 交给正文抽取器。
11. 所有拒绝原因、URL发现决策、正文身份结果、重复熔断分组和后置相关性结果必须写入审计文件。

### 新增审计

- `data/audit/news_content_gate.json`
  - 正文解析阶段拒绝记录；
  - 每个抽取方法的结构质量与身份质量；
  - URL发现接受/拒绝原因；
  - 共享 URL/正文哈希熔断分组；
  - 后置相关性硬门禁拒绝记录。
- `data/audit/paper_post_enrichment_gate.json`
  - 文献全文/摘要补全后的相关性拒绝记录。
- `data/audit/retrieval_funnel.json`
  - 新增 `content_circuit_rejected` 和 `post_enrichment_rejected` 计数。

### 主要改动文件

- `src/pifactory/content.py`
- `src/pifactory/relevance.py`
- `src/pifactory/pipeline.py`
- `src/pifactory/news.py`
- `src/pifactory/browser_fetch.py`
- `tests/test_news_content_gate_v11.py`

### 回归测试必须覆盖

- 标题含 hantavirus、正文为 W3C XML 命名空间：拒绝。
- 页面为全站导航，仅侧栏出现一次 hantavirus：拒绝。
- 合法新闻正文多次提及病原体并含事件上下文：接收。
- 普通媒体页中的 W3C/其他站点侧栏外链不得进入候选队列。
- 合法 RSS 实质摘要可兜底；无病原身份的长摘要必须拒绝。
- `relevance_post_enrichment=reject` 必须真实删除记录。
- 不同标题复用同一 URL/正文哈希：整组熔断。
- 同一事件的相似标题和同一正文：只保留质量最高的一条。
- 修复项 01 的真实发表日期门禁测试必须继续通过。

## 修复项 03：七要素/五要素大量输出占位文本

### 状态

已在修复项 01 与 02 的工程基础上实现；专项测试、日期门禁测试和全量回归测试通过。

### 根因

1. `analyze_paper()` / `analyze_news()` 只在 LLM 成功时保存 `LLMResult.attempts`；全部失败后仅保留被截断的异常字符串，无法判断系统性 fallback 的主因。
2. Gemini/Groq 未配置、认证失败、限流、配额、超时、无候选、非法 JSON 和 validator 失败均被合并为同一种 `LLMError` 表现。
3. 工作流运行前没有最小 JSON 健康探针，只有“环境变量是否存在”的静态检查。
4. fallback 角色规则过窄，且按背景/设计优先处理，方法句和结果句可能被前面的宽泛字段抢走。
5. fallback 没有位置与数字特征救援，角色关键词未命中时直接输出固定“未报告”文案。
6. 分析 fallback 比例没有全局统计、阈值和页面顶部警告。
7. 长证据包没有独立的分析提示词字符预算，可能增加模型截断和非法 JSON 风险。
8. 修复前的分析缓存仍可能继续复用旧 `fallback_source_extract`。

### 不可回退规则

1. 每次 LLM 尝试必须结构化记录 provider、model、任务、耗时、输入字符、状态、失败类型和安全错误摘要。
2. LLM 全部失败时，`LLMError` 必须携带完整安全 `attempts`；不得仅抛出不可解析字符串。
3. 失败原因必须稳定分类，至少包括未配置、认证、限流、配额、超时、网络、服务不可用、上下文/输出超限、非法 JSON、validator 失败和空响应。
4. GitHub Actions 在处理 21 个 profile 前必须执行 Gemini/Groq 最小 JSON preflight；审计不得包含密钥值。
5. fallback 比例达到 20% 必须显示全局警告，达到 50% 必须显示严重降级；默认阈值可通过环境变量调整。
6. 警告必须写入 Actions 日志、GitHub Pages 顶部和微信公众号正文顶部，不能只藏在单卡片底部。
7. `relevance/translation/overview` 等其他 LLM 调用不得破坏结构化尝试记录。
8. fallback 必须优先保留方法和结果证据，再处理设计、背景、解释和意义；同一证据句不得被多个字段重复消费。
9. 未命中显式角色时，必须综合关键词、数字/百分比和摘要相对位置进行救援；只有证据确实不存在时才输出明确缺失说明。
10. 分析证据超过字符预算时必须执行角色多样性压缩，并记录压缩审计。
11. 分析策略版本必须升级以自动失效修复前缓存。
12. 所有分析质量状态与逐条 fallback 原因必须写入 `data/audit/analysis_quality.json`。

### 新增审计

`data/audit/analysis_quality.json`

记录：

- 候选池与最终展示记录的 passed/fallback 数量及比例；
- 全局严重程度和中英文提示；
- fallback 主因分类；
- provider/model 尝试及失败分类；
- LLM preflight 结果；
- 逐条 fallback 标题、ID、策略、错误和 attempts；
- 提示词压缩前后字符数与证据条数。

### 主要改动文件

- `src/pifactory/llm.py`
- `src/pifactory/analysis.py`
- `src/pifactory/analysis_quality.py`
- `src/pifactory/http.py`
- `src/pifactory/config.py`
- `src/pifactory/pipeline.py`
- `src/pifactory/render.py`
- `scripts/check_credentials.py`
- `.github/workflows/daily-intelligence.yml`
- `tests/test_analysis_quality_v11.py`
- `ANALYSIS_QUALITY_GATE_V11_ZH.md`

### 回归测试必须覆盖

- Gemini/Groq 均未配置时产生结构化 `no_provider_configured` 尝试记录。
- validator 失败后 fallback 保留 provider/model/失败类别。
- 无结构摘要中的方法句和带数字结果句能够被正确救援。
- 长证据包压缩后仍保留背景、方法、结果和结论角色。
- fallback 比例超过阈值时严重程度和主因统计正确。
- 网页顶部在概览之前显示全局分析质量警告。
- `check_credentials.py --analysis-only` 能生成不含密钥值的安全 JSON 审计。
- 修复项 01 日期门禁与修复项 02 新闻正文门禁测试必须继续通过。

## 修复/增强项 04：多 LLM 免费额度轮换与低 Token 文献精读

### 状态

已在修复项01、02、03的工程基础上实现；新增五供应商路由、本地证据筛选、L1/L2/L3分析层级、成功缓存和供应商使用审计。

### 根因与需求

1. 原系统只有 Gemini/Groq 两个分析供应商，任一平台额度或服务异常都会显著提高系统性 fallback 风险。
2. OpenRouter、Mistral、SiliconFlow 已获得API Key，但工程没有适配器、模型发现、余额探针或自动轮换。
3. 原证据包可能把大量全文段落发送给模型，重复消耗免费Token。
4. 所有文献使用相同精读深度，没有把有限额度集中到最重要文献。
5. fallback也可能被缓存，导致服务恢复后仍复用低质量结果。
6. 缺少供应商级和模型级Token、额度、冷却与故障审计。

### 不可回退规则

1. Gemini、Groq、OpenRouter、Mistral、SiliconFlow必须由统一LLM路由管理。
2. 供应商顺序必须由环境变量控制，不得把单一固定顺序写死在业务代码中。
3. 402/明确余额不足视为配额耗尽；429只视为短期冷却，自动尝试其他模型或供应商。
4. 全文不得整体发送给LLM；只能在本地按章节、角色、数字、方法词和去重筛选短证据包。
5. L1只使用摘要；L2只用于有限Top-N全文证据增强；L3只用于更小Top-N跨供应商复核。
6. 默认全文增强Top-N为12，跨供应商复核Top-N为5，全文证据包不超过9000字符。
7. 默认最大输出Token降至1400；SiliconFlow reasoning/thinking默认关闭。
8. 分析缓存键必须绑定本地证据包和策略版本；默认只缓存validator通过的正式结果。
9. 运行前preflight必须检查全部五个供应商；支持余额接口的平台应记录安全额度快照。
10. 每次运行必须输出 `data/audit/llm_provider_usage.json`，记录供应商/模型状态与Token使用。
11. API Key不得写入配置、日志、网页、缓存或审计文件。
12. 修复项01日期门禁、修复项02新闻正文门禁和修复项03分析质量告警必须继续通过。

### 主要改动文件

- `src/pifactory/llm.py`
- `src/pifactory/provider_state.py`
- `src/pifactory/evidence_selector.py`
- `src/pifactory/analysis.py`
- `src/pifactory/config.py`
- `src/pifactory/pipeline.py`
- `src/pifactory/overview.py`
- `src/pifactory/relevance.py`
- `src/pifactory/translation.py`
- `scripts/check_credentials.py`
- `.github/workflows/daily-intelligence.yml`
- `tests/test_multillm_low_token_v12.py`
- `MULTI_LLM_LOW_TOKEN_V12_ZH.md`

### 回归测试必须覆盖

- 五个供应商均进入可配置任务池。
- SiliconFlow额度不足后自动切换到Groq或下一供应商。
- L1证据包绝不能包含全文文本。
- L2证据包必须在字符预算内，并保留方法和结果角色。
- 分析策略版本升级，旧缓存自动失效。
- 工作流必须暴露OpenRouter、Mistral、SiliconFlow Secrets及低TokenVariables。
- 修复项01—03专项测试必须继续通过。

## 修复/增强项 05：统计口径透明化与两仓生产工程化

### 状态

已在修复项01—04基础上实现；相关性复核、内容门禁、Top-N限制和最终展示现在分别记录，两个仓库的安装、更新、Runner、测试、运行和回滚脚本已统一。

### 根因与需求

1. `after_final_gate` 到 `displayed` 之间包含正文补全、正文身份/主题复核、分析、翻译和Top-N截断，但旧网页只显示“相关性通过 → 最终纳入”。
2. `PIF_MAX_PAPERS/PIF_MAX_NEWS` 是展示篇幅限制，不是相关性门禁；旧页面容易让读者误以为中间记录异常丢失。
3. 旧审计只写最终展示记录，无法逐条核查Top-N之外的合格记录。
4. `anchor_coverage.json`生产端使用 `concept_count/concepts/executed`，旧渲染端仍读取遗留的 `identity_count/identities/queries_executed`。
5. 两仓安装文档和管理脚本版本分散，存在覆盖本地Conda环境或使用系统Python的风险。

### 不可回退规则

1. `retrieval_funnel.json`必须分别记录 `after_final_gate`、`ready_before_top_n`、`top_n_limit`、`top_n_excluded` 和 `displayed`。
2. 网页和公众号必须明确写出Top-N是按优先级、证据强度、时效性和来源质量排序后的展示限制。
3. 不得把Top-N未展示记录描述为不相关、删除或抓取失败。
4. Top-N前全部合格记录必须写入 `eligible_papers.jsonl`、`eligible_news.jsonl` 和 `display_selection.json`。
5. 审计JSONL每次运行前必须清空，避免重跑时重复追加旧记录。
6. 核心概念覆盖渲染必须读取当前 `concept_count/concepts/executed` Schema。
7. 公开仓路径固定为 `$HOME/github-projects/pathogen-intelligence-factory`。
8. 私有仓路径固定为 `$HOME/pathogen-wechat-publisher/repository`，运行状态固定在 `$HOME/pathogen-wechat-publisher/runtime`。
9. Conda初始化固定使用 `/home/stone/20T/DataBase/SoftwaresEnsembel/MiniConda/etc/profile.d/conda.sh`；私有发布器固定直接调用 `$HOME/pathogen-wechat-publisher/conda-env/bin/python`。
10. 同步脚本必须排除 `.git`、Conda环境、runtime和Runner目录。
11. 两仓失败隔离：公众号发布失败不得影响公开仓Pages和数据分支。
12. 中英文切换必须同时切换文献/新闻要素精读，而不仅是标题和摘要。

### 新增审计

```text
data/audit/display_selection.json
data/audit/eligible_papers.jsonl
data/audit/eligible_news.jsonl
```

### 主要改动文件

- `src/pifactory/pipeline.py`
- `src/pifactory/render.py`
- `scripts/issue_summary.py`
- `scripts/doctor_local.sh`
- `scripts/run_profile_local.sh`
- `.github/workflows/daily-intelligence.yml`
- `tests/test_statistics_transparency_v13.py`
- `tests/test_issue_summary_v13.py`
- `docs/STATISTICS_AND_SELECTION_V13_ZH.md`
- 两仓包根目录安装、管理、验证和回滚脚本

### 回归测试必须覆盖

- 相关性通过、Top-N前可展示、Top-N排除和最终展示数量互不混淆。
- 网页与公众号使用相同统计定义。
- `eligible_*.jsonl`覆盖全部Top-N前可展示记录。
- 核心概念覆盖不再显示错误的0个锚点。
- 英文模式显示英文七/五要素。
- 两仓安装脚本不删除本地环境、runtime、Runner或封面状态。
- 修复项01—04全部专项测试继续通过。

### 两仓失败隔离（v13生产整理）

- 公开仓在数据分支写入成功后，向私有仓发送 `repository_dispatch` 只能采用 best-effort 方式。
- 私有仓Token缺失、GitHub API连接失败或私有Runner离线，不得中断公开仓其余病毒、门户构建或GitHub Pages部署。
- 私有仓失败通过Actions警告和私有仓运行日志单独处理；已经生成的数据和网页不得回滚。
