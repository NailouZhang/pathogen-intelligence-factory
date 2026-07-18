# v7 全文与新闻正文补全政策

## 1. 补全发生在 Top-50 之后

在候选检索、Python/LLM 相关性复核、去重和排序阶段，只使用数据库返回的标题、元数据、摘要、开放获取标志和新闻摘要。完成 Top-50 选择后，才访问全文、PDF、出版者公开页面或新闻落地页。

因此：

- 不为数百条候选下载 PDF；
- 不为被去重或拒绝的新闻抓取正文；
- 内容补全失败不影响候选检索审计；
- 深度分析只处理最终展示集合。

## 2. 合法开放获取链

文献内容补全按以下顺序尝试：

1. Europe PMC/PMC Open Access JATS XML；
2. PMC BioC；
3. 数据库已提供的 Crossref 或 Europe PMC 全文链接；
4. OpenAlex `best_oa_location`；
5. Unpaywall `best_oa_location` 与其他开放位置；
6. DOI 或出版者公开落地页面；
7. 原始数据库摘要。

下载后必须通过 DOI、标题和作者身份校验，防止抓到错误论文。

工程只使用合法开放获取和公开页面，不集成绕过付费墙、访问控制或版权限制的来源。未获得开放全文时，使用数据库摘要进行分析，并标记 `E1`；获得合法全文时标记 `E2`；只有元数据时标记 `E0`。

## 3. 新闻正文

只对最终最多 50 条新闻解析落地页，使用 JSON-LD、Trafilatura、`article/main` 和 meta description 逐级提取。正文长度不足或页面阻止访问时，保留 RSS/API 摘要并记录 `partial/unavailable`。
