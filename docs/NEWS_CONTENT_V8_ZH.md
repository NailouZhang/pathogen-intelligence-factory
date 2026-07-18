# v8 新闻正文抓取与 500 字微信摘要

## URL 处理

按顺序尝试原始 URL、resolved_url、canonical_url 和备选 URL，记录重定向后真实地址。

## 正文提取

```text
JSON-LD articleBody
→ Trafilatura favor_precision
→ Trafilatura favor_recall
→ article/main DOM
→ 常见正文段落
→ html2txt / 可见段落
```

RSS excerpt 只能作为检索阶段元数据，不能冒充原始报道正文。

## 正文质量门禁

- 至少 320 字符。
- 至少两个句子。
- 不能与标题高度相似。
- 不能主要由导航、Cookie、版权或订阅提示构成。
- 应存在目标病毒身份或合法限定上下文。

## 长新闻精炼

原始正文可保留到分析证据上限。单篇新闻 LLM 从正文提取五要素，并生成 55～170 词英文简报；翻译后用固定字段预算形成最多 500 中文字符的微信摘要：

```text
时间
地点与对象
事件
影响与风险
应对与不确定性
```

## 失败处理

正文失败记录标记为 `excerpt_only`、`title_only_rejected` 或 `unavailable`，不进入新闻分析和综合汇总；从最多 20 条补位候选中补齐。
