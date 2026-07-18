# v9 API Key、Variables 和本地凭据

公开仓 Secrets：`CROSSREF_MAILTO`、`NCBI_API_KEY`、`GEMINI_API_KEY`、`GROQ_API_KEY`、`OPENALEX_API_KEY`、`PUBLISHER_REPO_TOKEN`；可选 `UNPAYWALL_EMAIL`、`SEMANTIC_SCHOLAR_API_KEY`。

公开仓 Variables：`RELIEFWEB_APPNAME=wiv-virology-literature-tracker-42x`、`PUBLISHER_REPO`、模型名称、封面模式、复核模式、单 profile 超时、汇总条数和补位池大小。

不需要 Google Cloud Translation，也不使用 Google CSE。微信 AppID/AppSecret 只存本地 `runtime/config/publisher.env`，不存 GitHub Secrets。
