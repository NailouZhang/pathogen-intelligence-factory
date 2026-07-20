# pathogen-intelligence-factory v16.1

公开仓负责“病原文献与新闻抓取—规范化—跨库去重—内容补全—相关性终审—双语结构化分析—GitHub Pages—不可变微信公众号发布包”。私有公众号仓只消费公开仓生成的 `pathogen-wechat-package/v2`，两条发布链互不阻塞。

## 固定位置与协议

```text
GitHub: NailouZhang/pathogen-intelligence-factory
本地仓库: /home/stone/github-projects/pathogen-intelligence-factory
Conda Prefix: /home/stone/github-projects/pathogen-intelligence-factory/.conda-env
数据分支: intelligence-data
公众号协议: pathogen-wechat-package/v2
issue schema: 6.2
```


## v16.1 微信公众号48,000字符多级兜底

微信公众号发布包先使用每条新闻不超过500字符的微信专用简报，再按以下顺序逐项重渲染和重新计数：末位主文献详情精简、末位主新闻简报省略、补充新闻RSS简讯省略、末位补充文献卡片省略、末位补充新闻卡片省略，最后才允许极端省略末位主新闻卡片。所有省略只影响微信公众号正文；GitHub Pages、latest.json、审计和完整目录不删除。微信开头公开显示总数、展示数和因篇幅未展开数量。

预算审计写入：

```text
wechat-package/content-budget-audit.json
data/audit/wechat_content_budget.json
```

## v15.3文献生命周期

```text
5个冻结病毒身份词
→ 七个学术源独立检索
→ 初始字段/日期规范化与明显超窗门禁
→ 文章对象门禁和宽松身份初筛
→ DOI/PMID/PMCID/标题作者跨库去重
→ 合并来源日期与元数据
→ 去重后分批内容补全
→ 三态内容身份核验
→ 补全后重新计算规范发表日期并再次门禁
→ 补全后相关性终审
→ 分析、翻译与动态递补
→ 形成最多100篇可比较主报告候选
→ 全局重排后选择Top50
→ 其余终审通过记录进入补充文献Top100
→ 顶部总结只使用最终主报告Top50
```

### 三种身份结论

```text
identity_verified   标识符一致，或标题+作者+期刊+年份形成足够一致证据
identity_uncertain  未发现冲突，但可核验信息不足
identity_conflict   DOI/PMID/PMCID明确冲突或综合身份明显不一致
```

`identity_conflict`不可被后续弱匹配覆盖；它会阻止错误摘要或正文写入记录。

## 五个核心词与Profile冻结

每个 `profiles/<profile_id>/seed.yaml` 必须满足：

```yaml
search_strategy:
  core_terms_version: "2.0"
  frozen: true
  allow_weekly_mutation: false
  concepts:               # 恰好5个
    - scholarly: "独立病毒身份词或固定疾病/综合征名称"
```

禁止：

- `virus + outbreak/vaccine/surveillance/diagnosis/treatment` 等研究方向组合；
- `AND/OR/NOT`、括号和长布尔表达式；
- 普通症状、蛋白、宿主、设备、软件或机构名称；
- 五个上下位关系高度重复的近义词。

正常周运行、定时运行和 `run-all` 不会调用LLM改写五词。只有操作员显式提交 `refresh_profile=true` 时，才允许依据权威来源生成一个新的冻结版本。

## 后置词库的实际用途

Profile中的后置词库已经接入运行路径：

| 词库 | 运行用途 |
|---|---|
| `identity_terms` | 初筛、终审、标题/摘要/全文身份命中 |
| `qualified_abbreviations` | 缩写上下文校验 |
| `exclusion_terms` | 同名非目标实体排除 |
| `paper_priority_terms` | `paper_priority_tier`和全局Top50排序 |
| `document_type_terms` | 研究、综述、病例、方法、评论等分类 |
| `controlled_supplemental_terms` | 少量成员病毒的受控补充召回 |

受控补充词不会替代五个入口词，也不会扩张为整个后置词库。实际执行记录写入：

```text
data/audit/controlled_supplemental_queries.json
data/audit/query_plan.json
data/audit/query_coverage.json
```

## 日期与去重

规范发表日期优先级固定为：

```text
first_publication_date
→ online_date
→ published_date
→ print_date
```

`created_date/indexed_date`只用于审计。日期至少计算两次：

1. 初始门禁阻挡明显超窗记录；
2. 跨来源去重、元数据合并和内容补全后重新计算并再次过滤。

最终网页、历史去重和状态判断只使用第二次结果。

## bioRxiv与medRxiv

报告窗口内记录按首次发布日期倒序连续分页；每个平台最多读取最新300条。随后立即执行病毒身份初筛，不使用“第一页+尾页”的不连续抽样。

## 内容补全与短内容规则

内容补全只在跨库去重后执行：

```text
PMCID/PMC
→ PMID/PubMed和PMCID关联
→ Europe PMC
→ DOI/Crossref/OpenAlex/开放获取来源
→ 出版商落地页
```

不按字符长度删除记录。30字符但病毒身份明确、来源可靠且无排除实体的摘要，在LLM不可用时仍可进入确定性相关性判断。

404、登录页、Cookie页和JavaScript占位页只表示该次补全失败，不会覆盖已有可信标题、摘要或元数据。

仅有全文的主报告会从核验全文中抽取可追溯英文片段，写入 `full_text_excerpt`，再生成中文翻译；前台不会出现空白英文原文区。

## 主报告与补充文献

默认参数：

```text
PIF_MAX_PAPERS=50
PIF_MAX_FULLTEXTS=150
PIF_DISPLAY_CANDIDATE_BUFFER=100
PIF_FULLTEXT_BATCH_SIZE=25
PIF_MAX_SUPPLEMENTARY_PAPERS=100
```

系统以最多100篇完成分析和翻译的证据文献组成全局比较池，再统一重排Top50。达到50篇不会立即停止。

补充文献包含：

- 只有核验元数据且摘要/全文暂不可得的记录；
- 具有摘要或全文但没有进入全局Top50的终审相关记录。

补充卡片只展示中英文标题、作者、期刊、规范日期、DOI/PMID/PMCID、来源和客观状态，不生成摘要、七/五要素或研究结论。标题翻译依次尝试配置的翻译接口和LLM；全部失败后才回退英文标题。

## 新闻和微信公众号分流

新闻标准数据资格由日期、来源、正文有效性、病毒相关性和新闻质量决定。`PIF_WECHAT_NEWS_MAX_ZH_CHARS`只作用于公众号渲染，不参与新闻保留或删除。Pages和 `latest.json` 保存完整合格内容。

## 前台与后台

Pages依次展示：

```text
本期文献进展
本期新闻动态
研究论文主报告
综述主报告
补充文献目录
新闻
```

前台不显示LLM供应商、翻译器、抓取器、fallback比例等内部质量横幅。完整信息保存在 `data/audit/`。

## 本地开发与测试

```bash
source "/home/stone/20T/DataBase/SoftwaresEnsembel/MiniConda/etc/profile.d/conda.sh"
conda activate /home/stone/github-projects/pathogen-intelligence-factory/.conda-env

cd /home/stone/github-projects/pathogen-intelligence-factory
python -m pip install --no-build-isolation --no-deps -e .
python -m pytest -q
python scripts/validate_all_profiles.py
python scripts/audit_query_coverage.py
```

## 手动运行单个Profile

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  --ref main \
  -f profile_id=hantavirus \
  -f dispatch_wechat=false \
  -f refresh_profile=false \
  -f cover_image_mode=auto \
  -f review_mode=balanced
```

公开网页验收后，将 `dispatch_wechat` 攒为 `true`，即可触发私有仓本地Runner。


## v15.3测试与新闻来源状态

公开仓pytest可在独立克隆目录运行，不依赖两仓工程根目录。测试默认禁用真实Playwright网络；浏览器测试显式注入HTML。Google/Bing聚合页只有解析到出版商最终地址后才允许形成`full/partial`，否则使用`syndicated_summary`或拒绝标题项。

## v16.0 生产增强

v16.0 增加 Profile 150 分钟硬上限、阶段墙钟预算和 30 分钟最终化保留；文献先形成全局比较池，最多分析 150 次并在 50 篇成功后停止；新增语义指纹、一次性终审词库及 Profile 变化联动重建；LLM 默认 Gemini 首选、Groq 最终兜底；新闻支持一次生成简报与五要素以及 `supplementary_news`；微信包执行 48,000 可见字符硬审计。

详细安装、更新和十六项验收见完整两仓发行包中的 `V16_OPTIMIZATION_AND_ARCHITECTURE_ZH.md`、`INSTALL_CONFIG_RUN_V16_ZH.md` 和 `VALIDATION_REPORT_V16.txt`。
