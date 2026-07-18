# v7 系统架构

## 1. 两仓边界

### 公开仓：pathogen-intelligence-factory

负责：

- 21 个病毒 profile 与固定权威来源；
- 每个 profile 的 5 个核心检索概念；
- PubMed、Europe PMC、Crossref、Semantic Scholar、OpenAlex、bioRxiv、medRxiv 和新闻检索；
- Python 全候选相关性复核；
- Gemini/Groq 边界复核；
- 去重、热点与重要性排序；
- 最终 Top 50 文献和 Top 50 新闻的合法内容补全；
- 双语翻译、五要素分析、综合概览；
- GitHub Pages；
- `wechat-package/v2`；
- 通过 `repository_dispatch` 通知私有发布仓。

### 私有仓：pathogen-wechat-publisher

负责：

- 本地 Ubuntu self-hosted runner；
- 下载公开仓 `intelligence-data` 分支指定 40 位 SHA 的发布包；
- Schema、路径和 SHA-256 校验；
- 微信公网 IP、AppID/AppSecret 和来源仓白名单检查；
- 封面 SHA-256 复用或更新；
- 正文图片上传；
- `draft/add` 创建草稿；
- `publish_key` 防重复；
- 只创建草稿，不自动群发。

两个仓库独立失败、独立提交、独立回滚。公开抓取失败不会修改私有发布代码；微信发布失败不会撤销公开 Pages 数据。

## 2. 每日执行链

```text
北京时间 02:00
→ 读取 weekly_virus_schedule.yaml
→ 当天 3 个 profile 顺序执行
→ 载入 seed.yaml 与缓存 profile
→ 必要时读取固定权威网页并精炼富词库
→ 编译 5 个 provider-native 核心查询概念
→ 多学术源与多新闻源并发检索
→ 时间窗过滤
→ Python 轻量相关性闸门
→ DOI/PMID/标题/作者/年份/URL 去重
→ Python 100% 富词复核
→ Gemini/Groq 仅判断边界记录
→ 重要性、热点和来源收敛排序
→ Top 50 文献与 Top 50 新闻
→ 合法开放全文/公开页面/新闻正文补全
→ 五要素、翻译和综合概览
→ 写入 intelligence-data
→ 构建 GitHub Pages
→ 可选触发私有微信仓
```

## 3. 性能边界

- 初始检索概念：每个 profile 最多 5 个；
- 不对富词库逐词发起检索；
- 候选元数据不做本地随机截断；
- Python 复核覆盖 100% 候选；
- 日常 LLM 模式为 `balanced`，仅复核边界记录；
- 全文/PDF/新闻正文补全：只处理最终展示集合，文献最多 50、新闻最多 50；
- 深度分析与翻译：只处理最终展示集合；
- 单 profile 默认最大运行时间 90 分钟；
- 输出实时阶段日志，外部接口失败记录到 `source_status.json`。
