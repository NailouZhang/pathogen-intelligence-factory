# 21 种病毒每周循环

工作流每天 `18:00 UTC` 启动，对应下一日北京时间 `02:00`。程序使用 `Asia/Shanghai` 判断真实星期，随后在同一个 Job 中按列表顺序逐个处理 3 个主题。

| 北京时间 | 顺序 |
|---|---|
| 周一 | `seasonal_influenza` → `sars_cov_2` → `respiratory_syncytial_virus` |
| 周二 | `human_metapneumovirus` → `human_adenovirus` → `human_enterovirus` |
| 周三 | `norovirus` → `measles_virus` → `human_papillomavirus` |
| 周四 | `dengue_virus` → `chikungunya_virus` → `avian_influenza` |
| 周五 | `hantavirus` → `sftsv` → `mpox_virus` |
| 周六 | `nipah_virus` → `arenaviridae` → `ebola_viruses` |
| 周日 | `marburg_virus` → `rabies_virus` → `hepatitis_b_virus` |

后续只需编辑 `config/weekly_virus_schedule.yaml`。校验规则要求：

- 7 天完整；
- 每天恰好 3 个 profile；
- 全周恰好 21 个唯一 profile；
- 每个 profile 必须有对应 `profiles/<profile_id>/seed.yaml`；
- 同一天按 YAML 顺序串行运行，不使用矩阵并行。
