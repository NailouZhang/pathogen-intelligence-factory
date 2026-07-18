# weekly21 v8 架构

## 双仓边界

```text
公开仓 pathogen-intelligence-factory
├─ 21 个病原 profile
├─ 5 核心概念检索
├─ 文献/新闻 API 适配器
├─ Python 相关性复核、去重、排序
├─ Top-N 内容补全
├─ 单篇结构化分析
├─ 免费翻译链和 LLM 兜底
├─ 15～25 条文献综合汇总
├─ 15～25 条新闻综合汇总
├─ GitHub Pages
└─ wechat-package/v2

私有仓 pathogen-wechat-publisher
├─ repository_dispatch/workflow_dispatch
├─ 固定本地 repository
├─ 固定 Conda Python
├─ manifest/SHA-256 校验
├─ 封面与正文图片素材处理
├─ publish_key 防重复
└─ 微信 draft/add
```

公开仓失败不改变私有仓状态；私有发布失败不影响公开抓取和 Pages。

## 调度

| 星期（北京时间） | profile 1 | profile 2 | profile 3 |
|---|---|---|---|
| 周一 | seasonal_influenza | sars_cov_2 | respiratory_syncytial_virus |
| 周二 | human_metapneumovirus | human_adenovirus | human_enterovirus |
| 周三 | norovirus | measles_virus | human_papillomavirus |
| 周四 | dengue_virus | chikungunya_virus | avian_influenza |
| 周五 | hantavirus | sftsv | mpox_virus |
| 周六 | nipah_virus | arenaviridae | ebola_viruses |
| 周日 | marburg_virus | rabies_virus | hepatitis_b_virus |

## 数据分支

公开仓代码位于 `main`。每个成功 profile 的不可变结果提交到 `intelligence-data`：

```text
profiles/<profile_id>/data/
profiles/<profile_id>/site/
profiles/<profile_id>/wechat-package/
```

私有仓收到：

```text
source_repo + 40位 source_sha + package_path
```

因此发布器读取精确提交，不读取浮动的最新页面。

## v8 后处理边界

昂贵步骤只作用于展示候选：

```text
全量元数据候选
→ Python/LLM 相关性复核
→ 去重与排序
→ Top 50 + 20 条有界补位队列
→ 合法全文/新闻正文
→ 最终最多 50 + 50
→ 单篇分析、翻译、汇总
```

不会对全部检索记录抓全文、翻译或生成五/七要素。
