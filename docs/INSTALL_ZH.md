# pathogen-intelligence-factory v14 安装与GitHub部署

## 固定路径

```text
GitHub仓库：NailouZhang/pathogen-intelligence-factory
本地仓库：$HOME/github-projects/pathogen-intelligence-factory
Conda环境：$HOME/github-projects/pathogen-intelligence-factory/.conda-env
Conda初始化：/home/stone/20T/DataBase/SoftwaresEnsembel/MiniConda/etc/profile.d/conda.sh
```

## 1. 克隆

```bash
mkdir -p "$HOME/github-projects"
cd "$HOME/github-projects"
git clone git@github.com:NailouZhang/pathogen-intelligence-factory.git
cd pathogen-intelligence-factory
```

## 2. 创建固定前缀环境

```bash
CONDA_SH=/home/stone/20T/DataBase/SoftwaresEnsembel/MiniConda/etc/profile.d/conda.sh \
PIF_ENV_PREFIX="$HOME/github-projects/pathogen-intelligence-factory/.conda-env" \
bash scripts/bootstrap_dev.sh

"$HOME/github-projects/pathogen-intelligence-factory/.conda-env/bin/python" \
  -m playwright install --with-deps --only-shell chromium
```

不得使用 `conda create -n pathogen-intelligence-factory`。自动化始终直接调用固定前缀Python。

## 3. GitHub Secrets

```bash
cd "$HOME/github-projects/pathogen-intelligence-factory"

gh secret set CROSSREF_MAILTO
gh secret set UNPAYWALL_EMAIL
gh secret set NCBI_API_KEY
gh secret set GEMINI_API_KEY
gh secret set GROQ_API_KEY
gh secret set OPENROUTER_API_KEY
gh secret set MISTRAL_API_KEY
gh secret set SILICONFLOW_API_KEY
gh secret set OPENALEX_API_KEY
gh secret set SEMANTIC_SCHOLAR_API_KEY
gh secret set PUBLISHER_REPO_TOKEN
```

`PUBLISHER_REPO_TOKEN`必须能够向私有仓 `NailouZhang/pathogen-wechat-publisher`发送`repository_dispatch`。

## 4. GitHub Variables

```bash
gh variable set PUBLISHER_REPO --body 'NailouZhang/pathogen-wechat-publisher'
gh variable set PIF_PUBLICATION_FUTURE_DAYS --body '90'
gh variable set PIF_ANALYSIS_FULLTEXT_TOP_N --body '12'
gh variable set PIF_ANALYSIS_CROSSCHECK_TOP_N --body '5'
gh variable set PIF_ANALYSIS_EVIDENCE_MAX_CHARS --body '9000'
gh variable set PIF_ANALYSIS_MAX_PROMPT_CHARS --body '14000'
gh variable set PIF_LLM_MAX_OUTPUT_TOKENS --body '1400'
gh variable set PIF_ANALYSIS_FALLBACK_WARNING_RATIO --body '0.20'
gh variable set PIF_ANALYSIS_FALLBACK_CRITICAL_RATIO --body '0.50'
gh variable set PIF_LLM_CACHE_SUCCESS_ONLY --body 'true'
gh variable set PIF_LLM_DISABLE_THINKING --body 'true'
gh variable set PIF_LLM_PROVIDER_COOLDOWN_SECONDS --body '60'
gh variable set PIF_LLM_EXTRACT_PROVIDER_ORDER --body 'siliconflow,groq,mistral,openrouter,gemini'
gh variable set PIF_LLM_RESCUE_PROVIDER_ORDER --body 'gemini,mistral,openrouter,groq,siliconflow'
gh variable set PIF_LLM_OVERVIEW_PROVIDER_ORDER --body 'gemini,mistral,openrouter,groq,siliconflow'
gh variable set PIF_LLM_RELEVANCE_PROVIDER_ORDER --body 'groq,siliconflow,mistral,gemini,openrouter'
gh variable set PIF_OPENROUTER_FREE_ONLY --body 'true'
gh variable set PIF_NEWS_EVENT_QUERY_LIMIT --body '4'
gh variable set PIF_SCARCE_NEWS_PROFILES --body 'marburg_virus,nipah_virus,ebola_viruses,arenaviridae,sftsv'
gh variable set PIF_REJECT_PUBLICATION_TYPES --body 'dataset,data set,component,grant,supplementary material,supplement'
gh variable set PIF_REJECT_REPOSITORY_HOSTS --body 'figshare.com,zenodo.org,dryad.org,datadryad.org'
```

## 5. GitHub Pages

```bash
gh api --method POST \
  repos/NailouZhang/pathogen-intelligence-factory/pages \
  -f build_type=workflow || true
```

也可使用两仓包根目录：

```bash
bash public_manager.sh enable-pages
```

## 6. 凭据与本地诊断

```bash
"$HOME/github-projects/pathogen-intelligence-factory/.conda-env/bin/python" \
  scripts/check_credentials.py \
  --analysis-only \
  --probe-llm \
  --json-out /tmp/pif_llm_preflight.json

bash scripts/doctor_local.sh
```

## 7. 首次安全运行

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  --ref main \
  -f profile_id=hantavirus \
  -f dispatch_wechat=false \
  -f cover_image_mode=deterministic \
  -f review_mode=balanced

gh run watch --repo NailouZhang/pathogen-intelligence-factory
```

确认Pages和`intelligence-data`无误后，再把`dispatch_wechat`设为`true`。


### SiliconFlow中国站

```bash
gh variable set SILICONFLOW_BASE_URL --body 'https://api.siliconflow.cn/v1' --repo NailouZhang/pathogen-intelligence-factory
```
