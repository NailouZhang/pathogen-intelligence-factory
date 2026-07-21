# Pathogen Intelligence Factory v17.1

这是三仓系统的私有生产仓。它负责21种病毒的文献与新闻召回、去重、正文获取、相关性终审、双语分析、公开静态站生成、微信公众号发布包生成和后台审计。

v17.1 的生产词库全部内置于 `config/vocabularies/<profile_id>/`，包括 SARS-CoV-2 在内的21个 Profile 均重新生成。生产默认禁止把核心检索词当作最终终审词库。

主要改动：公开内容统一经过 `display_issue` 净化；顶部统计改为紧凑灰色小字；Literature Brief 与 News Brief 改为3至5个项目；新闻正文增加中文/英文导航、Cookie、栏目矩阵和重复推荐内容诊断；相关性终审按标题、摘要/简讯、正文分别设阈值，并提供三级终审恢复、低量输出和空结果合法输出；非英文来源通过多语言契约与二次审计隔离；Pages 改由独立公开仓部署。

## 本地安装

```bash
bash /home/stone/pathogen-wechat-publisher/releases/current/install_three_repos.sh install --run-tests
```

## 运行

```bash
TOOLS=/home/stone/pathogen-wechat-publisher/releases/current
bash "$TOOLS/factory_manager.sh" run-today true
bash "$TOOLS/factory_manager.sh" run-one sars_cov_2 false false false deterministic balanced
bash "$TOOLS/factory_manager.sh" publish-pages
```

## 测试

```bash
bash scripts/doctor_local.sh
/home/stone/github-projects/pathogen-intelligence-factory/.conda-env/bin/python -m pytest -q
```

完整安装、Token、三仓迁移和运行说明见工程包根目录 `INSTALL_CONFIG_RUN_V17_ZH.md`。
