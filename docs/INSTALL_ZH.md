# 公开仓库 v6 安装与升级

本文件只操作 `NailouZhang/pathogen-intelligence-factory`。完整逐条命令见 `docs/GITHUB_UPDATE_PUBLIC_ZH.md`。

核心顺序：公开仓打标签 → 运行完整包中的 `install_public_repo_update.sh` → 安装依赖 → 验证 21 个 profile 与查询覆盖 → pytest/compileall → 单独 commit/push → 配置 Secrets/Variables → 先运行 hantavirus 且 `dispatch_wechat=false` → 再运行全部 21 个 profile 且不推微信 → 检查 Pages 和 `data/audit`。

本版本必须设置 `OPENALEX_API_KEY`；Semantic Scholar Key 暂无时保持未设置，程序会以匿名降速模式执行全部编译查询。ReliefWeb Variable 使用 `wiv-virology-literature-tracker-42x`，审核前未授权响应会记录为 pending/skipped。

Pages 设置：`Settings → Pages → Build and deployment → Source → GitHub Actions`。
