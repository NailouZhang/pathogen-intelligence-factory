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

## 修复项 06：非叙述性缩写词表污染拦截

### 状态

已在修复项01—05基础上实现。狂犬病报告中 `Abbreviations/缩写` 段被分号切成伪句，并错误进入“主要结果、意义、局限”等字段的问题已修复。

### 不可回退规则

1. `split_sentences()`不得把分号作为硬句子边界。
2. 在任何角色分类、证据检索或fallback之前，必须截断终末 `Abbreviations:`、`List of abbreviations:`、`Acronyms:`、`缩写：`、`缩略语：`段。
3. 具有多个“短术语:定义”对的片段必须视为术语表，不得进入七/五要素候选句池。
4. 原始摘要可以保留完整文本供用户查看，但精读证据包必须使用清洗后的叙述性文本。

### 主要文件

- `src/pifactory/utils.py`
- `tests/test_v14_quality_and_bilingual.py`

## 修复项 07：Dataset、补充材料和仓储对象早期硬门禁

### 状态

已实现文献类型与仓储平台双重硬熔断，在相关性复核、全文抓取、翻译和LLM之前执行。

### 不可回退规则

1. `dataset`、`component`、`grant`、`supplementary material`等非论文类型不得进入候选文献池。
2. Figshare、Zenodo、Dryad及对应DOI前缀默认拒绝。
3. 标题含 `Dataset`、`Supplementary Material`、`source data`、`数据集`、`补充材料`等明确非论文信号时拒绝。
4. 每条拒绝记录必须写入 `data/audit/scholarly_record_type_gate.json`，不得静默丢弃。
5. 门禁必须在Python/LLM相关性复核之前执行，避免消耗API和全文抓取配额。

### 主要文件

- `src/pifactory/scholarly_gate.py`
- `src/pifactory/pipeline.py`
- `src/pifactory/render.py`
- `tests/test_v14_quality_and_bilingual.py`

## 修复项 08：结构化分析严格标量Schema

### 状态

已修复新闻嵌套字典被 `str(dict)` 强转后穿透校验、最终在HTML中显示Python字典字面量的问题。

### 不可回退规则

1. `_paper_validator()`和`_news_validator()`必须先执行类型校验，再执行清洗和长度校验。
2. 七/五要素每个值必须是非空字符串；`dict/list/tuple/set`一律拒绝。
3. `evidence_ids`每个字段必须是字符串列表，列表成员不得为嵌套对象。
4. `summary_en/brief_en`必须是字符串。
5. 校验失败必须继续尝试下一模型或供应商；全部失败后才进入确定性fallback。
6. 渲染层仍须HTML转义，但不得承担修复非法分析Schema的职责。

### 主要文件

- `src/pifactory/analysis.py`
- `tests/test_v14_quality_and_bilingual.py`

## 修复项 09：英文主分析与中英文平行实体

### 状态

用户问题4和问题8属于同一个数据层根因，已合并修复。分析只执行一次英文结构化抽取，保留为 `elements_en`；随后按字段翻译一次生成 `elements_zh`。公开网页为每个卡片生成独立中英文DOM镜像。

### 不可回退规则

1. 原生英文论文/新闻的LLM结构化输出必须保存在 `elements_en`。
2. 中文结构化要素必须从已校验的英文要素逐字段翻译，保存为 `elements_zh`，不得重新分析原文以避免双倍Token和事实分叉。
3. `analysis_en/analysis_zh`仅作为兼容别名，公开Schema以 `elements_en/elements_zh`为准。
4. 标题、摘要、七/五要素、统计、总览、来源健康、链接、审计和页脚均须具有中英文DOM容器。
5. JavaScript切换必须同时控制所有 `.lang-zh/.lang-en`，英文模式不得用固定占位符替代已有英文要素。
6. 微信公众号包保持中文单语，因为公众号正文不支持页面级JavaScript切换；GitHub Pages提供完整双语。

### 主要文件

- `src/pifactory/translation.py`
- `src/pifactory/render.py`
- `src/pifactory/overview.py`
- `prompts/literature_overview.md`
- `prompts/news_overview.md`
- `tests/test_v14_quality_and_bilingual.py`

## 修复项 10：DOI落地页与Unpaywall邮箱解耦

### 状态

已修复 `doi_landing`错误嵌套在`if doi and mailto`中的缩进问题。

### 不可回退规则

1. 只要存在DOI，就必须加入 `https://doi.org/{doi}` 候选落地页。
2. 只有Unpaywall API调用需要邮箱；未配置邮箱不得禁用通用DOI落地页。
3. 内容审计必须记录 `doi_landing`是否尝试及失败原因。

### 主要文件

- `src/pifactory/content.py`
- `tests/test_v14_quality_and_bilingual.py`

## 修复项 11：21病原顺序运行的共享额度与冷却状态

### 状态

已将内存态 `ProviderRuntimeState`升级为北京时间每日共享文件状态。排在后面的profile会继承前面profile已确认的认证失败、额度耗尽和冷却信息。

### 不可回退规则

1. GitHub工作流中的21种病原必须按计划顺序串行运行，不得默认并行争抢同一免费额度池。
2. 状态文件固定由 `PIF_PROVIDER_STATE_FILE`指定；生产工作流使用 `intelligence-data/shared/state/provider_quota_daily.json`的工作副本。
3. 状态以Asia/Shanghai自然日自动重置。
4. 401/403认证失败和明确额度耗尽在当日跨profile熔断；429按冷却期处理，不得永久判定额度耗尽。
5. 文件读写必须加进程锁并原子覆盖；审计不得包含API Key。
6. 本地顺序运行使用公开仓 `runtime/shared/provider_quota_daily.json`。

### 主要文件

- `src/pifactory/provider_state.py`
- `src/pifactory/llm.py`
- `.github/workflows/daily-intelligence.yml`
- `scripts/run_profile_local.sh`
- `tests/test_v14_quality_and_bilingual.py`

## 修复项 12：稀缺病原事件驱动新闻检索

### 状态

已实现从本周真实发表、通过论文类型门禁的文献中提取病原、地点、疫情/病例/死亡等事件线索，动态追加到新闻RSS、GDELT、ReliefWeb和WHO查询。

### 不可回退规则

1. 动态事件词只能来自本期真实发表日期窗口内的文献。
2. 事件来源文献必须先通过非论文对象门禁，并且文本中必须同时出现目标病原身份和事件词。
3. 动态查询必须包含目标病原和地点，不得只用泛化的 `outbreak/case`。
4. 马尔堡、尼帕、埃博拉、沙粒病毒、SFTSV等稀缺profile可将新闻相关性分数降低1分，但正文病原身份硬门禁和后置主题门禁不得放宽。
5. 动态查询和证据来源必须写入 `data/audit/event_query_expansion.json`。
6. 如果没有可信事件线索，系统继续使用原始检索词，不得虚构地点或事件。

### 主要文件

- `src/pifactory/event_query.py`
- `src/pifactory/pipeline.py`
- `src/pifactory/config.py`
- `.github/workflows/daily-intelligence.yml`
- `tests/test_v14_quality_and_bilingual.py`

## 修复项 13：作者与摘要元数据展示去重

### 状态

四份网页额外暴露了跨数据库作者全名/缩写重复、Importance段落重复和转义HTML标签残留。本项作为展示与Token卫生修复纳入v14。

### 不可回退规则

1. 作者合并必须识别 `Hade Ramos` 与 `Ramos H`、`Pranav S. Pandit` 与 `Pandit PS`等全名/缩写变体，并优先保留信息更完整的显示形式。
2. 摘要进入分析和翻译之前必须移除普通及HTML转义标签。
3. 只有规范化后完全相同的长句才去重；短科学短语不得因重复出现而随意删除。
4. 摘要末尾缩写词表继续遵守修复项06规则。
5. 去重后的摘要用于翻译、证据选择和页面展示，避免重复消耗Token。

### 主要文件

- `src/pifactory/utils.py`
- `src/pifactory/dedup.py`
- `tests/test_v14_quality_and_bilingual.py`

## 修复项 14：成品HTML渲染后质量硬门禁

### 状态

已新增独立的成品HTML审计器。每个profile完成页面渲染和公众号包校验后，必须再检查最终HTML，而不是只依赖中间JSON和单元测试。

### 不可回退规则

1. 成品HTML中任何七/五要素不得出现Python字典或列表字面量。
2. 英文结构化要素不得以 `Not reported in the supplied evidence.` 等批量固定占位符代替已经存在的英文分析结果。
3. 英文DOM中的结构化要素不得出现大段中文污染；专有名词和短中文引用除外。
4. Figshare、Zenodo、Dryad及明确Dataset/Supplement对象不得作为论文卡片进入成品页面。
5. `Abbreviations/缩写`之后的词表不得出现在七/五要素字段。
6. 公开页面必须同时包含中文和英文切换容器。
7. 发现关键问题时审计脚本退出非零，当前profile不得发布到Pages或触发公众号仓。
8. 审计结果必须写入 `data/audit/rendered_html_quality.json`。

### 主要文件

- `scripts/audit_rendered_html.py`
- `.github/workflows/daily-intelligence.yml`
- `tests/test_v14_quality_and_bilingual.py`


## 修复项 15：公开仓 src-layout 导入与测试启动契约

### 状态

已修复公开仓采用 `src/pifactory` 布局，但 pytest 配置错误地只加入仓库根目录，导致使用正式包名 `pifactory` 的测试在收集阶段报 `ModuleNotFoundError`。

### 不可回退规则

1. `pyproject.toml` 的 pytest 路径必须指向 `src`，工程代码、脚本和测试统一使用正式包名 `pifactory`；不得重新引入 `src.pifactory` 双命名空间。
2. 公开仓 Conda 引导脚本必须执行 `pip install --no-deps -e <repo>`，使 `pifactory` 不依赖当前工作目录即可导入。
3. 本地诊断脚本必须显式设置 `PYTHONPATH=<repo>/src:<repo>`，并在运行测试前验证 `pifactory.__file__` 指向当前仓库。
4. GitHub Actions 安装依赖后必须执行 editable install，保证本地和云端导入契约一致。
5. 自动化不得依赖用户当前已激活的 `(wechat-publisher)` 环境；公开仓测试固定使用 `<public-repo>/.conda-env/bin/python`。

### 主要文件

- `pyproject.toml`
- `scripts/bootstrap_dev.sh`
- `scripts/doctor_local.sh`
- `.github/workflows/daily-intelligence.yml`
- `tests/test_import_contract_v14_1.py`


## 修复项 15：事件驱动查询写入列表型 query_plan 导致生产崩溃（v14.2）

### 根因

`build_query_plan()` 的公开契约始终是 `list[dict]`，但 v14 在学术检索结束后错误执行了：

```python
plan["event_driven_news"] = event_query_plan
plan["scarce_news_mode"] = scarce_news_mode
```

非 demo 的生产流水线因此在完成耗时的数据库检索后触发 `TypeError: list indices must be integers or slices, not str`。

### 不可回退规则

1. `query_plan` 必须继续保持查询组列表，不能在运行中改成映射或对其使用字符串索引。
2. 动态事件查询必须作为普通查询组追加到列表，保留旧审计和消费端兼容性。
3. `event_query_expansion` 与 `scarce_news_mode` 继续作为 issue/audit 的独立顶层字段保存。
4. 必须测试列表契约、动态查询追加、重复查询去重和错误映射输入。
5. 工作流生产路径必须在学术检索完成后仍可进入新闻检索，不得只测试 demo 分支。


## 修复项16：GitHub Actions构建后端与临时更新器（v14.3）

### 现象
GitHub Actions在执行editable安装时抛出`BackendUnavailable: Cannot import setuptools.build_meta`，业务流水线尚未开始即退出。

### 根因
`pyproject.toml`声明`setuptools.build_meta`，但工作流只升级pip并使用`--no-build-isolation`，没有保证当前Python环境先安装setuptools和wheel。CI工作流也仅安装运行依赖，没有验证项目包安装契约。

### 不可回退规则
1. 构建工具必须先于任何editable/regular项目安装完成并通过导入检查。
2. GitHub Actions、CI和本地Conda引导必须调用同一个安装器。
3. 公开仓环境继续固定为仓库内`.conda-env`，不得创建命名环境。
4. 更新ZIP只在`/tmp`解压，更新器不得依赖版本化根目录名称。
5. Actions在进入耗时检索前必须输出项目版本与`pifactory`导入路径。

### 实现
- 新增`requirements-build.txt`。
- 新增`scripts/install_python_project.sh`。
- `daily-intelligence.yml`和`ci.yml`统一调用共享安装器。
- `bootstrap_dev.sh`复用同一安装器。
- 新增`tests/test_build_install_contract_v14_3.py`。
- `update_from_tmp.sh`改为自动发现工程根目录。

## 修复项17：HTML质量审计器空元素栈泄漏（v14.4）

### 生产故障

真实 GitHub Actions 流水线已经完成抓取、分析、翻译、页面生成和微信公众号发布包校验，但 `audit_rendered_html.py` 将第二张及后续中文卡片错误识别为英文内容，产生 106 条 `chinese_text_in_english_element` 并以退出码 2 阻断发布。

### 根因

Python `HTMLParser` 对 `<br>`、`<img>`、`<meta>` 等 void element 不会调用 `handle_endtag()`。旧审计器却把所有开始标签压入自维护栈，英文摘要中的 `<br>` 因而永久保留 `lang_en=True`。关闭外层英文容器时旧代码只删除中间一个栈元素，没有同时移除其后代，导致后续中文 `<dd>` 继承英文作用域。

### 不可回退规则

1. HTML void element 不得压入结构栈。
2. 结束标签必须从栈顶弹出到目标标签，不能只删除中间单个元素。
3. 语言作用域只继承结构父元素；同时继承中英文时必须报告 `ambiguous_language_scope`。
4. 文献和新闻卡数量只统计 `<article class="card paper|news">`，不能把栏目包装器计入新闻卡。
5. 成品审计必须包含至少两张卡片且第一张英文摘要含 `<br>` 的回归测试。

### 回归测试

- `tests/test_render_audit_void_elements_v14_4.py`
- 保留原有字典字面量、英文占位符、仓储对象和双语渲染测试。
