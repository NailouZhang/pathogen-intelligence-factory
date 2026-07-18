# v9 新闻正文恢复与质量门禁

候选 URL 来源包括 RSS 原始链接、entry.links、RSS HTML 外链、GDELT/ReliefWeb 直接 URL、canonical、OpenGraph、JSON-LD、mainEntityOfPage、脚本内嵌 URL、AMP 和文章正文外链。

正文提取顺序：JSON-LD articleBody → Trafilatura precision → Trafilatura recall → article/main → 常见正文选择器 → 可见段落。每条新闻最多尝试 10 个候选 URL。

原文正文失败时，RSS/转载摘要只有满足以下条件才能降级为 `syndicated_summary`：不少于 140 字符、不少于 24 个词、至少两句或不少于 260 字符、与标题不高度相似、目标病原身份校验通过。标题、导航和 Cookie 文本不能通过。

最终新闻卡只使用 `full`、`partial` 或 `syndicated_summary`。公众号中文简报仍限制在 500 字以内，并明确其证据状态。
