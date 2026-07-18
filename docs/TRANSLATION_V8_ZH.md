# v8 免费翻译与不轮空策略

## 顺序

```text
1. deep-translator 的 GoogleTranslator
2. Python requests 调用免费的 Google 翻译兼容入口
3. deep-translator 的 MyMemoryTranslator
4. Gemini/Groq 最终兜底
5. 翻译质量门禁
```

不配置 Google Cloud Translation，不产生该服务的付费依赖。

## 分块

长摘要按句子切分为约 2600 字符的块，逐块翻译后按原顺序合并，不截断摘要前部。

## 术语保护

在送入 Python 翻译器前，将病毒名、分类名和关键专业词替换为占位 token；译文返回后恢复固定中文词表，防止汉坦病毒等名称被误译。

## 校验

- 中文字符比例。
- 译文不为空且不等于原文。
- 数字、百分比、P 值、区间和单位不丢失。
- 保护 token 全部恢复。
- 禁止错误病原翻译和“翻译暂不可用”占位符。

## 发布门禁

标题、摘要/新闻简报和全部分析字段均成功才设置 `translation_ready=true`。翻译全部失败的记录不进入页面和微信包；不会显示空白或错误占位语。
