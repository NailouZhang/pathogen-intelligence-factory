# v10 API Key、Secrets 与 Variables

## 公开仓 Secrets

- `CROSSREF_MAILTO`：Crossref polite pool 与 Unpaywall 邮箱回退；
- `UNPAYWALL_EMAIL`：可选，未设置时使用 `CROSSREF_MAILTO`；
- `NCBI_API_KEY`；
- `GEMINI_API_KEY`；
- `GROQ_API_KEY`；
- `OPENROUTER_API_KEY`；
- `MISTRAL_API_KEY`；
- `SILICONFLOW_API_KEY`；
- `OPENALEX_API_KEY`；
- `SEMANTIC_SCHOLAR_API_KEY`：当前没有可不设置；
- `PUBLISHER_REPO_TOKEN`：仅授权私有仓 Contents Read and write 的 Fine-grained PAT。

## 公开仓 Variables

- `RELIEFWEB_APPNAME=wiv-virology-literature-tracker-42x`；
- `PUBLISHER_REPO=NailouZhang/pathogen-wechat-publisher`；
- `PIF_LLM_REVIEW_MODE=balanced`；
- `PIF_OVERVIEW_MIN_ITEMS=15`；
- `PIF_OVERVIEW_MAX_ITEMS=25`；
- `PIF_DISPLAY_CANDIDATE_BUFFER=30`；
- `PIF_NEWS_BROWSER_ENABLED=true`；
- `PIF_NEWS_BROWSER_MAX_PAGES=3`；
- `PIF_NEWS_BROWSER_TIMEOUT_MS=18000`；
- `PIF_NEWS_STATIC_MAX_URLS=8`；
- `PIF_NEWS_ENRICH_WORKERS=4`；
- `PIF_NEWS_EXCERPT_MIN_CHARS=100`；
- `PIF_WECHAT_NEWS_MAX_ZH_CHARS=500`。

Playwright 不需要 API Key。Google Cloud Translation 未启用，也不需要其计费凭据。


## 修复项04：多供应商与低Token变量

- `PIF_ANALYSIS_FULLTEXT_TOP_N=12`；
- `PIF_ANALYSIS_CROSSCHECK_TOP_N=5`；
- `PIF_ANALYSIS_EVIDENCE_MAX_CHARS=9000`；
- `PIF_ANALYSIS_MAX_PROMPT_CHARS=14000`；
- `PIF_LLM_MAX_OUTPUT_TOKENS=1400`；
- `PIF_LLM_CACHE_SUCCESS_ONLY=true`；
- `PIF_LLM_DISABLE_THINKING=true`；
- `PIF_LLM_EXTRACT_PROVIDER_ORDER=siliconflow,groq,mistral,openrouter,gemini`；
- `PIF_LLM_RESCUE_PROVIDER_ORDER=gemini,mistral,openrouter,groq,siliconflow`。
