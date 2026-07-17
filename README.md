# Pathogen Intelligence Factory — 15种病毒每周循环版

公开 GitHub 仓库，按北京时间每日 02:00 顺序分析当天清单中的病毒。每种病毒每周一次，检索窗口为过去 7 天；最多展示质量排序后的前 50 篇文献和前 50 条权威新闻。

## 默认周计划

- 周一：Arenaviridae、Hantavirus、Mpox Virus
- 周二：SFTSV、SARS-CoV-2
- 周三：Nipah virus、Ebola virus
- 周四：Norovirus、Chikungunya virus
- 周五：Influenza Virus、Rhinovirus
- 周六：Parainfluenza Virus、Enterovirus
- 周日：Respiratory Syncytial Virus、Human Metapneumovirus

调度只需修改 `config/weekly_virus_schedule.yaml`。每个病原的简单种子词在 `profiles/<profile_id>/seed.yaml`。

## 运行链路

```text
周清单 → 严格术语档案 → 多源文献/新闻 → 相关性与去重
→ 高水平/权威排序 → 翻译与五要素 → 独立病原网页和封面
→ intelligence-data 不可变提交 → 私有发布仓库 → 本地 Runner
→ 微信公众号草稿
```

## 关键变化

- 每日 02:00 北京时间启动，单 Job 严格顺序执行；
- 15 个内置 profile；
- `max_papers=50`、`max_news=50`；
- Google CSE 可选，DuckDuckGo 与站内搜索回退；
- GitHub Pages 根目录变为 15 病原门户，各病原报告位于 `/profiles/<profile_id>/`；
- 封面支持 CJK 字体，不显示日期或时间；
- `pathogen-wechat-publisher` 合约不随病原变化。

详见：

- `docs/WEEKLY_15_VIRUSES_ZH.md`
- `docs/AUTHORITY_DISCOVERY_ZH.md`
- `docs/INSTALL_ZH.md`
