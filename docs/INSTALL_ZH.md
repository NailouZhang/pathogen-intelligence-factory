# weekly21 v9 双仓系统完整安装、更新、配置和运行

## 1. 默认路径

```text
完整包：$HOME/下载/pathogen-weekly21-v9-complete-bundle.zip
公开仓：$HOME/github-projects/pathogen-intelligence-factory
私有仓：$HOME/pathogen-wechat-publisher/repository
发布系统根目录：$HOME/pathogen-wechat-publisher
解压目录：/tmp/pathogen-weekly21-v9-bundle
```

## 2. 基础准备

```bash
sudo apt-get update
sudo apt-get install -y git unzip rsync curl
gh auth status || gh auth login
ssh -T git@github.com

cd "$HOME/下载"
chmod +x pathogen-weekly21-v9_public_manager.sh \
          pathogen-weekly21-v9_private_manager.sh
```

## 3. 公开仓升级

```bash
cd "$HOME/下载"
bash pathogen-weekly21-v9_public_manager.sh extract
bash pathogen-weekly21-v9_public_manager.sh tag
bash pathogen-weekly21-v9_public_manager.sh sync

cd "$HOME/github-projects/pathogen-intelligence-factory"
git status
git diff --stat

bash "$HOME/下载/pathogen-weekly21-v9_public_manager.sh" test
bash "$HOME/下载/pathogen-weekly21-v9_public_manager.sh" commit
```

`sync` 使用 `rsync --delete`，但保留 `.git`。脚本在目标仓存在未提交修改时拒绝覆盖；升级前标签格式为 `before-weekly21-v9-YYYYMMDD-HHMMSS`。

## 4. 公开仓 Secrets

```bash
bash "$HOME/下载/pathogen-weekly21-v9_public_manager.sh" configure-secrets
```

依次设置：

```text
CROSSREF_MAILTO                 必需
UNPAYWALL_EMAIL                 可选；空值时复用 CROSSREF_MAILTO
NCBI_API_KEY                    必需
GEMINI_API_KEY                  必需
GROQ_API_KEY                    必需
OPENALEX_API_KEY                必需
SEMANTIC_SCHOLAR_API_KEY        可选；当前没有可跳过
PUBLISHER_REPO_TOKEN            跨仓触发私有仓所需
```

`PUBLISHER_REPO_TOKEN` 使用 Fine-grained PAT，仅选择 `pathogen-wechat-publisher`，Repository permissions 中 `Contents: Read and write`。

## 5. Variables

```bash
bash "$HOME/下载/pathogen-weekly21-v9_public_manager.sh" configure-vars
```

写入：

```text
RELIEFWEB_APPNAME=wiv-virology-literature-tracker-42x
PUBLISHER_REPO=NailouZhang/pathogen-wechat-publisher
PIF_COVER_IMAGE_MODE=auto
PIF_LLM_REVIEW_MODE=balanced
PIF_PROFILE_RUNTIME_MINUTES=90
PIF_OVERVIEW_MIN_ITEMS=15
PIF_OVERVIEW_MAX_ITEMS=25
PIF_WECHAT_NEWS_MAX_ZH_CHARS=500
PIF_DISPLAY_CANDIDATE_BUFFER=30
```

ReliefWeb 审核未完成时，其 401/403 会被记录为 pending/skipped，不阻断其他来源。

## 6. GitHub Pages

仓库进入 `Settings → Pages → Build and deployment → Source → GitHub Actions`。工作流使用 `upload-pages-artifact` 和 `deploy-pages` 发布组合门户。

## 7. 单病毒验收

首次升级后不要立即推微信：

```bash
bash "$HOME/下载/pathogen-weekly21-v9_public_manager.sh" \
  run-one hantavirus false true deterministic balanced
sleep 5
bash "$HOME/下载/pathogen-weekly21-v9_public_manager.sh" watch
```

检查运行日志至少出现：

```text
display_selection
news_url_resolution
news_body_extraction
translation_gate
paper_ready_pool
news_ready_pool
final selected
```

检查数据包：

```bash
bash "$HOME/下载/pathogen-weekly21-v9_public_manager.sh" check-package hantavirus
```

日常重跑不重建词库：

```bash
bash "$HOME/下载/pathogen-weekly21-v9_public_manager.sh" \
  run-one hantavirus false false deterministic balanced
```

全部 21 个初始化：

```bash
bash "$HOME/下载/pathogen-weekly21-v9_public_manager.sh" \
  run-all false true deterministic balanced
```

按当天北京时间计划运行并推微信：

```bash
bash "$HOME/下载/pathogen-weekly21-v9_public_manager.sh" \
  run-today true auto balanced
```

## 8. 私有仓升级

```bash
cd "$HOME/下载"
bash pathogen-weekly21-v9_private_manager.sh tag
bash pathogen-weekly21-v9_private_manager.sh sync
bash pathogen-weekly21-v9_private_manager.sh bootstrap
bash pathogen-weekly21-v9_private_manager.sh test
bash pathogen-weekly21-v9_private_manager.sh commit
```

已有本地微信配置时不必重新执行 `configure-local`。首次配置：

```bash
bash pathogen-weekly21-v9_private_manager.sh configure-local
```

配置文件：

```text
$HOME/pathogen-wechat-publisher/runtime/config/publisher.env
```

## 9. Runner

已安装 Runner：

```bash
bash "$HOME/下载/pathogen-weekly21-v9_private_manager.sh" restart-runner
bash "$HOME/下载/pathogen-weekly21-v9_private_manager.sh" runner-status
```

从未注册时才执行：

```bash
bash "$HOME/下载/pathogen-weekly21-v9_private_manager.sh" setup-runner
sudo loginctl enable-linger "$USER"
```

## 10. 测试公众号草稿

```bash
bash "$HOME/下载/pathogen-weekly21-v9_private_manager.sh" check-package hantavirus
bash "$HOME/下载/pathogen-weekly21-v9_private_manager.sh" draft hantavirus true false
sleep 5
bash "$HOME/下载/pathogen-weekly21-v9_private_manager.sh" watch
```

`force=true` 允许重建同一发布日期草稿；`refresh_cover=false` 复用相同封面素材。

## 11. 回滚

```bash
cd "$HOME/github-projects/pathogen-intelligence-factory"
git tag --list 'before-weekly21-v9-*'
git revert <v9提交SHA>
git push
```

私有仓使用相同方式回滚其独立提交，不要把两仓操作混为一个提交。
