# 公开仓库完整安装与同步

## 建仓

```bash
unzip pathogen-intelligence-factory.zip -d "$HOME"
cd "$HOME/pathogen-intelligence-factory"
git init
git branch -M main
git add .
git commit -m "Initial pathogen intelligence factory"
gh repo create NailouZhang/pathogen-intelligence-factory --public --source . --remote origin --push
```

## Secrets

```bash
gh secret set CROSSREF_MAILTO --repo NailouZhang/pathogen-intelligence-factory
gh secret set NCBI_API_KEY --repo NailouZhang/pathogen-intelligence-factory
gh secret set GEMINI_API_KEY --repo NailouZhang/pathogen-intelligence-factory
gh secret set GROQ_API_KEY --repo NailouZhang/pathogen-intelligence-factory
gh secret set PUBLISHER_REPO_TOKEN --repo NailouZhang/pathogen-intelligence-factory
```

Fine-grained PAT 仅选择私有仓库 `NailouZhang/pathogen-wechat-publisher`，赋予 Contents: Read and write，用于创建 repository dispatch。

## Variables

```bash
gh variable set PIF_PROFILE_ID --body hantavirus --repo NailouZhang/pathogen-intelligence-factory
gh variable set PUBLISHER_REPO --body NailouZhang/pathogen-wechat-publisher --repo NailouZhang/pathogen-intelligence-factory
gh variable set GEMINI_IMAGE_MODEL --body gemini-3.1-flash-image --repo NailouZhang/pathogen-intelligence-factory
gh variable set PIF_COVER_IMAGE_MODE --body auto --repo NailouZhang/pathogen-intelligence-factory
```

## Pages

在仓库 Settings → Pages 中选择 GitHub Actions。首次运行：

```bash
gh workflow run daily-intelligence.yml --repo NailouZhang/pathogen-intelligence-factory -f profile_id=hantavirus -f refresh_profile=true -f cover_image_mode=auto -f dispatch_wechat=true
gh run watch --repo NailouZhang/pathogen-intelligence-factory
```

## 后续同步

```bash
cd "$HOME/pathogen-intelligence-factory"
git pull --ff-only
git add .
git commit -m "Update intelligence pipeline"
git push
```

## 新病原

```bash
python scripts/create_profile.py --profile-id lassa-virus --term "Lassa virus" --name-en "Lassa virus" --name-zh "拉沙病毒"
git add profiles/lassa-virus/seed.yaml
git commit -m "feat: add Lassa virus profile"
git push
gh workflow run daily-intelligence.yml --repo NailouZhang/pathogen-intelligence-factory -f profile_id=lassa-virus -f refresh_profile=true
```

私有发布仓库无需改代码，但本地 `ALLOWED_SOURCE_REPOS` 必须包含这个公开仓库；病原变化不需要新增源仓库。
