# 公开仓库 v6 独立升级、配置与运行命令

以下命令只操作：

```text
NailouZhang/pathogen-intelligence-factory
```

## 1. 解压完整工程包

```bash
rm -rf /tmp/pathogen-weekly21-v6-bundle

unzip \
  "$HOME/下载/pathogen-weekly21-v6-complete-bundle.zip" \
  -d /tmp
```

解压后目录：

```text
/tmp/pathogen-weekly21-v6-bundle
```

## 2. 为当前公开仓稳定版本打标签

```bash
cd "$HOME/github-projects/pathogen-intelligence-factory"

git status

git tag \
  -a before-weekly21-v6-$(date +%Y%m%d) \
  -m "Stable public version before weekly21 v6"

git push origin --tags
```

## 3. 同步 v6 公开仓文件

```bash
cd /tmp/pathogen-weekly21-v6-bundle

PUBLIC_REPO_DIR="$HOME/github-projects/pathogen-intelligence-factory" \
  bash install_public_repo_update.sh
```

该脚本不执行 git add、commit 或 push。

## 4. 安装依赖并离线验证

```bash
cd "$HOME/github-projects/pathogen-intelligence-factory"

python -m pip install -r requirements.txt

python scripts/validate_all_profiles.py
python scripts/audit_query_coverage.py
python scripts/check_credentials.py || true

python -m pytest -q
python -m compileall -q src scripts tests

bash -n scripts/*.sh 2>/dev/null || true
```

## 5. 检查修改并提交公开仓

```bash
cd "$HOME/github-projects/pathogen-intelligence-factory"

git remote set-url origin \
  git@github.com:NailouZhang/pathogen-intelligence-factory.git

git status
git diff --stat
git diff -- .github/workflows/daily-intelligence.yml

git add .

git commit -m \
  "feat: add full-corpus review and single-anchor retrieval v6"

git push
```

## 6. 设置公开仓 Secrets

逐条执行，GitHub CLI 会提示输入值：

```bash
gh secret set CROSSREF_MAILTO \
  --repo NailouZhang/pathogen-intelligence-factory

gh secret set NCBI_API_KEY \
  --repo NailouZhang/pathogen-intelligence-factory

gh secret set GEMINI_API_KEY \
  --repo NailouZhang/pathogen-intelligence-factory

gh secret set GROQ_API_KEY \
  --repo NailouZhang/pathogen-intelligence-factory

gh secret set OPENALEX_API_KEY \
  --repo NailouZhang/pathogen-intelligence-factory

gh secret set PUBLISHER_REPO_TOKEN \
  --repo NailouZhang/pathogen-intelligence-factory
```

当前没有 Semantic Scholar Key，不需要设置。取得后再运行：

```bash
gh secret set SEMANTIC_SCHOLAR_API_KEY \
  --repo NailouZhang/pathogen-intelligence-factory
```

## 7. 设置 Variables

```bash
gh variable set RELIEFWEB_APPNAME \
  --body 'wiv-virology-literature-tracker-42x' \
  --repo NailouZhang/pathogen-intelligence-factory

gh variable set PUBLISHER_REPO \
  --body 'NailouZhang/pathogen-wechat-publisher' \
  --repo NailouZhang/pathogen-intelligence-factory

gh variable set PIF_COVER_IMAGE_MODE \
  --body auto \
  --repo NailouZhang/pathogen-intelligence-factory
```

删除已弃用 Google CSE：

```bash
gh secret delete GOOGLE_CSE_API_KEY \
  --repo NailouZhang/pathogen-intelligence-factory || true

gh secret delete GOOGLE_CSE_ID \
  --repo NailouZhang/pathogen-intelligence-factory || true
```

检查名称：

```bash
gh secret list \
  --repo NailouZhang/pathogen-intelligence-factory

gh variable list \
  --repo NailouZhang/pathogen-intelligence-factory
```

## 8. 单独测试汉坦病毒，不推送微信

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  -f profile_id=hantavirus \
  -f refresh_profile=true \
  -f cover_image_mode=auto \
  -f dispatch_wechat=false
```

查看运行：

```bash
sleep 5

RUN_ID="$(
  gh run list \
    --repo NailouZhang/pathogen-intelligence-factory \
    --workflow daily-intelligence.yml \
    --event workflow_dispatch \
    --limit 1 \
    --json databaseId,createdAt \
    --jq 'sort_by(.createdAt) | last | .databaseId'
)"

echo "RUN_ID=$RUN_ID"

gh run watch "$RUN_ID" \
  --repo NailouZhang/pathogen-intelligence-factory
```

查看完整日志：

```bash
gh run view "$RUN_ID" \
  --repo NailouZhang/pathogen-intelligence-factory \
  --log \
  | tee "/tmp/public-v6-run-${RUN_ID}.log"
```

## 9. 初始化或重建全部 21 个 profile，不推送微信

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  -f run_mode=all \
  -f refresh_profile=true \
  -f cover_image_mode=auto \
  -f dispatch_wechat=false
```

该运行会顺序处理 21 个 profile，可能持续较长时间。

## 10. 检查数据分支最新提交

```bash
SOURCE_SHA="$(
  gh api \
    repos/NailouZhang/pathogen-intelligence-factory/commits/intelligence-data \
    --jq '.sha'
)"

echo "SOURCE_SHA=$SOURCE_SHA"
```

检查汉坦病毒包：

```bash
gh api \
  "repos/NailouZhang/pathogen-intelligence-factory/contents/profiles/hantavirus/wechat-package?ref=$SOURCE_SHA" \
  --jq '.[].name'
```

## 11. 后续正常调度

GitHub cron 为 `0 18 * * *`，对应北京时间次日 02:00。程序使用 `Asia/Shanghai` 的实际星期选择当天 3 个 profile，并在同一个 Job 中按 YAML 顺序运行。
