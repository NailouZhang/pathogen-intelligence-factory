# v8 API Key、Secrets 和 Variables

## 公开仓 Secrets

```text
CROSSREF_MAILTO               必填；Crossref polite identification，亦可供 Unpaywall
UNPAYWALL_EMAIL               可选；为空时回退 CROSSREF_MAILTO
NCBI_API_KEY                  必填
GEMINI_API_KEY                必填；分析、汇总、最终翻译兜底
GROQ_API_KEY                  必填；Gemini 文本任务回退
OPENALEX_API_KEY              必填
SEMANTIC_SCHOLAR_API_KEY      可选；空值时匿名限速模式
PUBLISHER_REPO_TOKEN          必填；仅授权私有发布仓 Contents read/write
```

不需要：

```text
Google CSE
Google Cloud Translation
Google Cloud service account
```

## 公开仓 Variables

```text
RELIEFWEB_APPNAME=wiv-virology-literature-tracker-42x
PUBLISHER_REPO=NailouZhang/pathogen-wechat-publisher
PIF_COVER_IMAGE_MODE=auto
PIF_LLM_REVIEW_MODE=balanced
PIF_PROFILE_RUNTIME_MINUTES=90
PIF_OVERVIEW_MIN_ITEMS=15
PIF_OVERVIEW_MAX_ITEMS=25
PIF_WECHAT_NEWS_MAX_ZH_CHARS=500
PIF_DISPLAY_CANDIDATE_BUFFER=20
```

## 私有本地配置

位置：

```text
$HOME/pathogen-wechat-publisher/runtime/config/publisher.env
```

字段：

```text
WECHAT_APP_ID
WECHAT_APP_SECRET
ALLOWED_SOURCE_REPOS
GITHUB_TRUST_ENV=false
EXPECTED_PUBLIC_IP
VERIFY_COVER_REMOTE=true
WECHAT_AUTHOR=
MAX_REPORT_AGE_HOURS=48
```

微信密钥不写入 GitHub Secrets，不提交到仓库。
