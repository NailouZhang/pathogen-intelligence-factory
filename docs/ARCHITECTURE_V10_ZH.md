# weekly21 v10 架构

## 两仓职责

### 公开仓 `NailouZhang/pathogen-intelligence-factory`

1. 按北京时间每周计划选择病毒；
2. 使用每个病毒约五个核心概念检索 PubMed、Europe PMC、Crossref、Semantic Scholar、OpenAlex、bioRxiv/medRxiv；
3. 从 Google News RSS、Bing News RSS、GDELT、ReliefWeb 和 WHO 获取新闻候选；
4. Python 全候选相关性复核、跨来源去重和排序；
5. 只对 Top 50 与有界补位池做摘要、开放全文和新闻正文补全；
6. 对研究、综述和新闻逐篇生成严格结构化解读；
7. 免费 Python 翻译优先，Gemini/Groq 最终兜底；
8. 在 15～25 条内分别生成文献进展和新闻动态；
9. 构建 GitHub Pages、数据审计和 `wechat-package/v2`；
10. 使用 `repository_dispatch` 通知私有仓。

### 私有仓 `NailouZhang/pathogen-wechat-publisher`

本地 Ubuntu self-hosted Runner 使用固定项目目录和固定 Conda Python，下载公开仓 `intelligence-data` 分支的精确 40 位 SHA，校验 manifest 与 SHA-256，复用或上传封面与正文图片，调用微信公众号 `draft/add`，仅创建草稿，不自动群发。

## v10 后处理顺序

```text
元数据候选
→ 时间窗过滤
→ Python 相关性复核
→ DOI/PMID/标题/URL 去重
→ LLM 边界复核
→ 时效、质量、热点和来源收敛排序
→ Top 50 + 补位池内容补全
→ 研究/综述/新闻逐篇结构化精读
→ 跨字段去重与完整句修复
→ 免费翻译链与翻译门禁
→ 文献15～25篇综合报道
→ 新闻15～25条官方通报式汇总
→ 独立统计概览行
→ Pages 与微信发布包
```
