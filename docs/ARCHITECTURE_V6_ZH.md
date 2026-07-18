# Pathogen Intelligence Factory v6 双仓架构

## 公开仓职责

`NailouZhang/pathogen-intelligence-factory` 维护 21 个 profile、每周调度、固定权威网页、词库、数据库适配器、全文/正文补全、全候选相关性复核、质量等级、翻译、五要素、Pages 和 `wechat-package/v2`。

## 私有仓职责

`NailouZhang/pathogen-wechat-publisher` 接收公开仓的数据提交 SHA 和包路径，由本地 Runner 下载不可变包，校验 manifest 和哈希，复用或更新封面，上传正文图片并创建微信公众号草稿。

## 主链路

```text
人工主题边界 + 固定权威来源
→ 专业词库
→ 数据库专属单锚点与组合查询
→ 多源候选汇总
→ 全候选 Python 粗审
→ 内容补全
→ 全候选紧凑 LLM 复核
→ U 类证据升级
→ 最终相关性闸门
→ A/B/C 质量排序
→ Top 50 + Top 50 深度解读
→ GitHub Pages
→ intelligence-data SHA
→ 本地 Runner
→ 微信草稿箱
```

## 失败隔离

- 单一数据库失败不会终止其他来源；
- 单个病毒失败不阻断同日后续病毒；
- Pages 部署失败不应撤销已经生成的数据包；
- 微信发布失败不影响公开仓内容与 Pages；
- 来源审计区分成功零结果、失败、跳过和部分失败。
