# 安装与升级

## 本地开发环境

```bash
cd "$HOME/github-projects/pathogen-intelligence-factory"
bash scripts/bootstrap_dev.sh
```

默认 Conda 初始化：

```text
/home/stone/20T/DataBase/SoftwaresEnsembel/MiniConda/etc/profile.d/conda.sh
```

## GitHub Secrets

```bash
gh secret set CROSSREF_MAILTO --repo NailouZhang/pathogen-intelligence-factory
gh secret set NCBI_API_KEY --repo NailouZhang/pathogen-intelligence-factory
gh secret set GEMINI_API_KEY --repo NailouZhang/pathogen-intelligence-factory
gh secret set GROQ_API_KEY --repo NailouZhang/pathogen-intelligence-factory
gh secret set PUBLISHER_REPO_TOKEN --repo NailouZhang/pathogen-intelligence-factory
```

可选：

```bash
gh secret set SEMANTIC_SCHOLAR_API_KEY --repo NailouZhang/pathogen-intelligence-factory
gh secret set GOOGLE_CSE_API_KEY --repo NailouZhang/pathogen-intelligence-factory
gh secret set GOOGLE_CSE_ID --repo NailouZhang/pathogen-intelligence-factory
```

Variables：

```bash
gh variable set PUBLISHER_REPO --body NailouZhang/pathogen-wechat-publisher --repo NailouZhang/pathogen-intelligence-factory
gh variable set PIF_COVER_IMAGE_MODE --body auto --repo NailouZhang/pathogen-intelligence-factory
gh variable set GEMINI_IMAGE_MODEL --body gemini-3.1-flash-image --repo NailouZhang/pathogen-intelligence-factory
```

## Pages

仓库 `Settings → Pages → Source` 选择 `GitHub Actions`。

## 首次测试

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  -f profile_id=hantavirus \
  -f refresh_profile=true \
  -f dispatch_wechat=true
```

## 全清单首次建库

会消耗更多 API 配额，建议确认单病原成功后执行：

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  -f run_mode=all \
  -f refresh_profile=true \
  -f dispatch_wechat=false
```

随后定时工作流会每日只运行当天 2–3 个病原，并正常触发公众号草稿。
