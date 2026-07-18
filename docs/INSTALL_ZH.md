# weekly21 v10 双仓系统完整安装、更新、配置和运行

## 默认路径

```text
完整包：$HOME/下载/pathogen-weekly21-v10-complete-bundle.zip
公开仓：$HOME/github-projects/pathogen-intelligence-factory
私有仓：$HOME/pathogen-wechat-publisher/repository
发布根目录：$HOME/pathogen-wechat-publisher
解压目录：/tmp/pathogen-weekly21-v10-bundle
```

## 基础准备

```bash
sudo apt-get update
sudo apt-get install -y git unzip rsync curl
gh auth status || gh auth login
ssh -T git@github.com
cd "$HOME/下载"
chmod +x pathogen-weekly21-v10_public_manager.sh pathogen-weekly21-v10_private_manager.sh
```

## 公开仓升级

```bash
cd "$HOME/下载"
bash pathogen-weekly21-v10_public_manager.sh extract
bash pathogen-weekly21-v10_public_manager.sh tag
bash pathogen-weekly21-v10_public_manager.sh sync
bash pathogen-weekly21-v10_public_manager.sh install-browser
bash pathogen-weekly21-v10_public_manager.sh test
bash pathogen-weekly21-v10_public_manager.sh commit
bash pathogen-weekly21-v10_public_manager.sh configure-vars
```

现有 Secrets 不变；首次部署或凭据变化时运行：

```bash
bash pathogen-weekly21-v10_public_manager.sh configure-secrets
```

`sync` 使用 `rsync --delete`，保留 `.git`，并在目标仓有未提交修改时拒绝覆盖。升级前会生成并推送 `before-weekly21-v10-YYYYMMDD-HHMMSS` 标签。

## GitHub Pages

仓库 `Settings → Pages → Build and deployment → Source` 选择 `GitHub Actions`。已有 Pages 正常时无需重设。

## 首次在线测试

不重建已缓存词库、不推微信，并使用确定性封面：

```bash
bash "$HOME/下载/pathogen-weekly21-v10_public_manager.sh"   run-one hantavirus false false deterministic balanced
sleep 5
bash "$HOME/下载/pathogen-weekly21-v10_public_manager.sh" watch
```

关注日志：`display_selection`、`display_content_enrichment`、`browser_attempts`、`deep_analysis`、`translation_gate`、`overview`、`pipeline complete`。

确认 Pages 后，触发微信链路：

```bash
bash "$HOME/下载/pathogen-weekly21-v10_public_manager.sh"   run-one hantavirus true false auto balanced
```

初始化全部 21 个 profile 仅在词库首次建立或主题定义改变时使用：

```bash
bash "$HOME/下载/pathogen-weekly21-v10_public_manager.sh"   run-all false true deterministic balanced
```

## 私有仓升级

```bash
cd "$HOME/下载"
bash pathogen-weekly21-v10_private_manager.sh tag
bash pathogen-weekly21-v10_private_manager.sh sync
bash pathogen-weekly21-v10_private_manager.sh bootstrap
bash pathogen-weekly21-v10_private_manager.sh test
bash pathogen-weekly21-v10_private_manager.sh commit
bash pathogen-weekly21-v10_private_manager.sh restart-runner
```

已有 `~/pathogen-wechat-publisher/runtime/config/publisher.env` 时不需要重新运行 `configure-local`。首次配置才运行：

```bash
bash pathogen-weekly21-v10_private_manager.sh configure-local
```

## 草稿测试

```bash
bash "$HOME/下载/pathogen-weekly21-v10_private_manager.sh" check-package hantavirus
bash "$HOME/下载/pathogen-weekly21-v10_private_manager.sh" draft hantavirus true false
sleep 5
bash "$HOME/下载/pathogen-weekly21-v10_private_manager.sh" watch
```

`force=true` 允许重新创建相同日期草稿；`refresh_cover=false` 在封面未改变时复用永久素材。

## 日常运行

```bash
# 单个病毒，不推微信
bash pathogen-weekly21-v10_public_manager.sh run-one hantavirus false false deterministic balanced

# 北京时间当天3个病毒并推微信
bash pathogen-weekly21-v10_public_manager.sh run-today true auto balanced

# 指定顺序的多个病毒
bash pathogen-weekly21-v10_public_manager.sh run-profiles 'hantavirus,sftsv,mpox_virus' true false auto balanced
```

定时工作流每天 UTC 18:00，即北京时间次日 02:00，在同一 Job 内顺序运行当天三个病毒。
