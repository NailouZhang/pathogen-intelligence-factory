# 15种病毒每周循环调度

## 默认计划

工作流每天在 `18:00 UTC` 启动，对应北京时间次日 `02:00`。程序使用 `Asia/Shanghai` 的真实星期解析清单，避免 UTC 星期偏移。

| 北京时间 | 顺序队列 |
|---|---|
| 周一 02:00 | arenaviridae → hantavirus → mpox-virus |
| 周二 02:00 | sfts-virus → sars-cov-2 |
| 周三 02:00 | nipah-virus → ebola-virus |
| 周四 02:00 | norovirus → chikungunya-virus |
| 周五 02:00 | influenza-virus → rhinovirus |
| 周六 02:00 | parainfluenza-virus → enterovirus |
| 周日 02:00 | respiratory-syncytial-virus → human-metapneumovirus |

调度文件：

```text
config/weekly_virus_schedule.yaml
```

列表顺序就是执行顺序。一个公开仓库 Job 内通过 Bash 循环逐个运行，因此不会同时分析多个病原。某个病原失败时会记录错误并继续下一个，避免阻断当天其他病原。

## 修改清单

1. 新增或修改 `profiles/<profile_id>/seed.yaml`。
2. 在 `config/weekly_virus_schedule.yaml` 中移动、增加或删除 profile ID。
3. 运行：

```bash
python scripts/resolve_weekly_schedule.py --mode all
python -m pytest -q tests/test_weekly_schedule.py tests/test_all_profiles.py
```

每个 profile 在全周只能出现一次；测试会拒绝重复项和缺失的 seed 文件。

## 手工运行

单个病原：

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  -f profile_id=nipah-virus \
  -f refresh_profile=true \
  -f cover_image_mode=auto \
  -f dispatch_wechat=true
```

指定顺序：

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  -f profiles='hantavirus,nipah-virus,ebola-virus' \
  -f dispatch_wechat=true
```

全部15种：

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  -f run_mode=all \
  -f dispatch_wechat=true
```

## 前50筛选

默认上限在调度 YAML 中：

```yaml
max_papers: 50
max_news: 50
```

论文优先考虑相关性、PubMed/Europe PMC 等来源、摘要和全文完整度、系统综述/Meta分析/临床试验/队列研究等研究设计、DOI、开放获取和时效性。

新闻优先考虑 WHO、CDC、ECDC、政府卫生部门、公共卫生机构、大学、医院和实验室等权威来源，其次考虑正文是否成功抓取、相关性与时效性。
