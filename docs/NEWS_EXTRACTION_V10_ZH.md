# v10 新闻正文多级提取

## 严格顺序

```text
feedparser RSS
→ 候选URL与发布时间
→ URL解码、跟踪参数清理、直接发布者URL优先
→ requests静态请求
→ JSON-LD articleBody
→ Trafilatura precision
→ Trafilatura recall
→ readability-lxml
→ newspaper3k
→ article/main/正文CSS/可见段落/html2txt
→ 标准Playwright Chromium渲染
→ 实质性来源摘要降级
```

## 内容门禁

正文必须具有足够长度、句子和独立词汇，不能只是标题、导航、Cookie、订阅、版权或广告文本。保存 `attempted_urls`、`browser_attempts`、提取器、正文长度、标题相似度和错误原因。

## Playwright边界

仅渲染公开可访问页面，不使用 stealth 插件、验证码处理、登录自动化、代理轮换或绕过付费墙/WAF。被阻止页面标记为 blocked/unavailable，并继续尝试合法候选。

## 输出

新闻正文逐篇提取时间、地点与人群、事件、规模影响与风险、应对状态与不确定性；公众号中文简报通过字段预算压缩到 500 字以内。
