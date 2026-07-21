# Factory v17.1 安装与运行

Factory 固定安装到 `/home/stone/github-projects/pathogen-intelligence-factory`，Conda Prefix 为仓内 `.conda-env`。推荐从完整三仓工程包根目录安装：

```bash
bash install_three_repos.sh install --run-tests
```

配置与运行统一使用：

```bash
TOOLS=/home/stone/pathogen-wechat-publisher/releases/current
bash "$TOOLS/factory_manager.sh" configure-vars
bash "$TOOLS/factory_manager.sh" configure-secrets
bash "$TOOLS/factory_manager.sh" commit
bash "$TOOLS/factory_manager.sh" run-today true
```

Factory 变为私有前，必须先验证 `NailouZhang/pathogen-intelligence-pages` 已成功公开部署。完整步骤见工程包根目录 `INSTALL_CONFIG_RUN_V17_ZH.md`。
