# pathogen-intelligence-factory v13 安装与GitHub部署

## 固定位置

```text
GitHub：NailouZhang/pathogen-intelligence-factory
本地：$HOME/github-projects/pathogen-intelligence-factory
Conda初始化：/home/stone/20T/DataBase/SoftwaresEnsembel/MiniConda/etc/profile.d/conda.sh
Conda环境：$HOME/github-projects/pathogen-intelligence-factory/.conda-env
```

## 安装依赖

```bash
cd "$HOME/github-projects/pathogen-intelligence-factory"
bash scripts/bootstrap_dev.sh
"$HOME/github-projects/pathogen-intelligence-factory/.conda-env/bin/python" -m playwright install --with-deps --only-shell chromium
bash scripts/doctor_local.sh
```

## GitHub Secrets

```text
CROSSREF_MAILTO
UNPAYWALL_EMAIL
NCBI_API_KEY
GEMINI_API_KEY
GROQ_API_KEY
OPENROUTER_API_KEY
MISTRAL_API_KEY
SILICONFLOW_API_KEY
OPENALEX_API_KEY
SEMANTIC_SCHOLAR_API_KEY
PUBLISHER_REPO_TOKEN
```

## 推荐Variables

```text
PUBLISHER_REPO=NailouZhang/pathogen-wechat-publisher
PIF_PUBLICATION_FUTURE_DAYS=90
PIF_ANALYSIS_FULLTEXT_TOP_N=12
PIF_ANALYSIS_CROSSCHECK_TOP_N=5
PIF_ANALYSIS_EVIDENCE_MAX_CHARS=9000
PIF_ANALYSIS_MAX_PROMPT_CHARS=14000
PIF_LLM_MAX_OUTPUT_TOKENS=1400
PIF_LLM_EXTRACT_PROVIDER_ORDER=siliconflow,groq,mistral,openrouter,gemini
PIF_LLM_RESCUE_PROVIDER_ORDER=gemini,mistral,openrouter,groq,siliconflow
PIF_ANALYSIS_FALLBACK_WARNING_RATIO=0.20
PIF_ANALYSIS_FALLBACK_CRITICAL_RATIO=0.50
PIF_ANALYSIS_REQUIRE_LLM=false
```

## GitHub Pages

仓库Settings中的Pages Source设为GitHub Actions。工作流将 `intelligence-data` 分支中的各病毒站点汇总为Pages artifact。

## 手动运行汉坦病毒

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  --ref main \
  -f profile_id=hantavirus \
  -f refresh_profile=false \
  -f cover_image_mode=deterministic \
  -f dispatch_wechat=false \
  -f review_mode=balanced
```

确认后开启公众号派发：

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  --ref main \
  -f profile_id=hantavirus \
  -f refresh_profile=false \
  -f cover_image_mode=auto \
  -f dispatch_wechat=true \
  -f review_mode=balanced
```

完整统计说明见 `docs/STATISTICS_AND_SELECTION_V13_ZH.md`，完整运行说明见 `docs/OPERATIONS_V13_ZH.md`。
