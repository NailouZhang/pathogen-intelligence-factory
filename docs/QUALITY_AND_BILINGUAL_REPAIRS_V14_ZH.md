# v14 网页质量、双语结构与稀缺新闻修复说明

## 1. 本次审查范围

本次修复以乙型肝炎病毒、狂犬病病毒、马尔堡病毒和汉坦病毒四份生产HTML为证据，逐条核对文献候选、新闻要素、fallback、双语DOM、全文候选和21病原顺序运行逻辑。

修复建立在01—05的基础上：

1. 真实发表日期门禁；
2. 新闻正文身份和错误页熔断；
3. LLM失败诊断与fallback告警；
4. 五供应商低Token路由；
5. 统计口径与两仓工程化。

本次新增修复项06—12。

## 2. 四份HTML确认的问题

### 2.1 缩写词表污染七要素

狂犬病论文摘要末尾包含以分号连接的 `Abbreviations:` 段。旧切句器把分号当句末，随后fallback把 `LAMP1:`、`MTOR:`、`qPCR:`定义误归类为主要结果、公共卫生意义和局限。

修复：

- 切句前识别并截断终末缩写/缩略语标题；
- 分号不再作为硬句界；
- 多个“短术语:定义”对被识别为术语表片段；
- 原始摘要仍完整展示，精读证据池只接收叙述性正文。

### 2.2 Dataset、Supplement和仓储对象冒充论文

马尔堡报告出现Figshare `Dataset of figures`，乙肝报告出现Figshare补充材料和Zenodo数据/分析对象。适配器已经取到 `publication_types`，但旧主流程没有消费该字段。

修复：

```text
真实发表日期门禁
→ 论文类型/仓储平台硬门禁
→ Python相关性
→ LLM边界复核
→ 全文与结构化分析
```

默认拒绝：

```text
dataset
data set
component
grant
supplementary material
supplement
Figshare
Zenodo
Dryad
```

每条拒绝记录保存在 `data/audit/scholarly_record_type_gate.json`。

### 2.3 新闻嵌套字典穿透校验

旧校验先调用 `clean_space(value)`，而该函数会执行 `str(value)`。模型返回嵌套字典时被转换为长字符串，从而绕过长度校验并在HTML中显示Python字典字面量。

修复：

- 所有结构化要素先检查 `isinstance(value, str)`；
- 字典、列表、元组、集合直接校验失败；
- evidence ID必须是字符串列表；
- 失败响应继续尝试下一模型/供应商；
- 所有供应商失败后才进入fallback。

### 2.4/2.8 英文卡片没有结构化镜像

问题4和问题8是同一个数据层缺陷：旧系统只有一份中文结构化分析，英文DOM只能读取不存在的英文要素，最终显示占位符或继续显示中文。

本次没有采用“同一个LLM请求同时生成中英两套长结果”，因为这会增加输出Token并形成两套可能不一致的事实表述。采用更稳定的英语主分析模式：

```text
英文原始证据
→ 一次英文结构化分析
→ 严格证据/Schema校验
→ elements_en
→ 逐字段一次性翻译
→ elements_zh
```

GitHub Pages每张卡片分别生成：

```html
<div class="lang-zh">...</div>
<div class="lang-en" hidden>...</div>
```

切换范围包括：标题、摘要、七/五要素、总览、统计、来源健康、链接、审计和页脚。

微信公众号正文保持中文，因为公众号草稿不支持网页JavaScript语言切换。

### 2.5 DOI落地页被邮箱条件误伤

旧 `doi_landing` 候选错误位于 `if doi and mailto:`内部。未配置Unpaywall邮箱时，基础DOI落地页也不再尝试。

修复后：

```python
if doi:
    candidates.append(("doi_landing", f"https://doi.org/{doi}"))
    if mailto:
        # Unpaywall查询
```

### 2.6 21病原共享额度状态未跨进程保存

工作流虽然按profile顺序运行，但每个Python进程原先都从“所有供应商健康”开始，后续profile重复请求已经耗尽的免费接口。

修复后：

```text
intelligence-data/shared/state/provider_quota_daily.json
```

保存当日：

- provider状态；
- model冷却；
- 认证失败；
- 配额耗尽；
- 请求和Token统计；
-安全余额快照。

状态按北京时间自然日重置。429只触发冷却；401/403和明确quota exhausted当日熔断。

### 2.7 马尔堡等稀缺病原新闻清零

马尔堡文献总览已经识别“埃塞俄比亚2025年疫情”，但新闻查询没有利用该事件信息，原新闻漏斗最终为0。

修复后，系统先从本周真实发表、非Dataset的文献中抽取：

```text
目标病原 + 地点 + outbreak/case/death/fatality等事件词 + 年份
```

再动态追加到Google/Bing RSS、GDELT、ReliefWeb和WHO查询。稀缺profile仅降低一分候选/复核阈值，正文身份与主题硬门禁保持不变。

### 2.9 作者、摘要和转义HTML的展示污染

网页中还出现同一作者的全名、首字母缩写同时展示，以及摘要/Importance长段重复。部分仓储对象中的 `&lt;b&gt;`、`&lt;i&gt;`在页面中作为文本显示。

修复：

- 作者键同时识别 `Given Surname` 与 `Surname Initials`，保留更完整的显示名；
- HTML先反转义再去标签；
- 规范化后完全相同的长句只保留一次；
- 清洗发生在翻译和证据选择之前，减少页面冗余和Token消耗。

## 3. 数据Schema

### 文献

```json
{
  "elements_en": {
    "research_question_and_background": "...",
    "study_design_and_population": "...",
    "methods": "...",
    "main_results": "...",
    "interpretation_and_novelty": "...",
    "scientific_and_public_health_significance": "...",
    "limitations_and_evidence_strength": "..."
  },
  "elements_zh": {
    "research_question_and_background": "..."
  }
}
```

### 新闻

```json
{
  "elements_en": {
    "time": "...",
    "location_and_population": "...",
    "event": "...",
    "scale_impact_and_risk": "...",
    "response_status_and_uncertainty": "..."
  },
  "elements_zh": {
    "time": "..."
  }
}
```

旧 `analysis_en/analysis_zh`保留为兼容别名，新的正式字段是 `elements_en/elements_zh`。

## 4. 新增审计

```text
data/audit/scholarly_record_type_gate.json
data/audit/event_query_expansion.json
data/audit/llm_provider_usage.json
data/audit/analysis_quality.json
data/audit/retrieval_funnel.json
```

## 5. 新增可配置项

```text
PIF_NEWS_EVENT_QUERY_LIMIT=4
PIF_SCARCE_NEWS_PROFILES=marburg_virus,nipah_virus,ebola_viruses,arenaviridae,sftsv
PIF_REJECT_PUBLICATION_TYPES=dataset,data set,component,grant,supplementary material,supplement
PIF_REJECT_REPOSITORY_HOSTS=figshare.com,zenodo.org,dryad.org,datadryad.org
PIF_PROVIDER_STATE_FILE=<shared daily state file>
```

## 6. 当前工程化代码仍存在的边界与不足

这些不属于本次确认的逻辑错误，但运行时必须明确：

1. 新闻网站HTML、反爬策略和重定向会变化，正文覆盖率无法保证100%；系统选择“少发而不误发”。
2. 免费LLM额度、模型名和限流规则可能随平台变化，必须依赖preflight和运行审计，不能假设固定可用。
3. 事件驱动新闻查询只利用本周文献已经出现的事件；没有文献线索的新突发事件仍依赖原始RSS、WHO、GDELT和ReliefWeb。
4. 平台黑名单会排除Figshare/Zenodo中的所有对象，包括少数可能具有科学价值的独立报告；被拒记录保留在审计中，可按profile显式调整变量，但不能默认放开。
5. `publication_types`在部分数据库中缺失，因此仍需结合平台、DOI和标题信号；元数据不完整时无法做到绝对零误判。
6. 中文要素是英文已验证要素的翻译镜像，不是第二次独立学术分析；这样可降低Token和避免事实分叉，但翻译质量仍需术语表与抽查。
7. 微信公众号正文为中文静态HTML，无法复用GitHub Pages的JavaScript双语切换。
8. 共享provider状态设计面向顺序工作流；若未来把21个profile改为并行矩阵，需要改成事务型状态更新或外部存储，不能直接复用当前“读取整表—保存整表”。
9. 本地Runner依赖学校网络出口IP、GitHub连接和微信白名单；任何一项变化都会影响草稿发布，但不会影响公开仓数据和Pages。
10. 离线测试不能替代真实数据库、五家LLM、GitHub Actions和微信API的在线验收；生产首轮必须查看全部审计文件。

## 7. 回归要求

每次后续修复必须继续通过：

```text
tests/test_publication_date_gate_v11.py
tests/test_news_content_gate_v11.py
tests/test_analysis_quality_v11.py
tests/test_multillm_low_token_v12.py
tests/test_statistics_transparency_v13.py
tests/test_v14_quality_and_bilingual.py
```
