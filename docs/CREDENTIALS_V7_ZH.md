# v7 API Key、Secrets 与 Variables

## 公开仓 Secrets

| 名称 | 状态 | 用途 |
|---|---|---|
| `CROSSREF_MAILTO` | 建议必配 | Crossref polite pool、User-Agent 联系方式；也可作为 Unpaywall 邮箱回退 |
| `UNPAYWALL_EMAIL` | 建议 | Unpaywall 合法 OA 查询邮箱，可与 CROSSREF_MAILTO 相同 |
| `NCBI_API_KEY` | 建议必配 | 提高 PubMed E-utilities 调用额度 |
| `GEMINI_API_KEY` | 完整功能必需 | 词库精炼、边界复核、五要素、翻译、概览、可选封面 |
| `GROQ_API_KEY` | 强烈建议 | Gemini 文本任务回退 |
| `OPENALEX_API_KEY` | OpenAlex 必需 | OpenAlex Works API |
| `SEMANTIC_SCHOLAR_API_KEY` | 可选 | 未配置时使用 5 条匿名降速查询 |
| `PUBLISHER_REPO_TOKEN` | 微信联动必需 | 向私有仓发送 repository_dispatch |

不再使用：

```text
GOOGLE_CSE_API_KEY
GOOGLE_CSE_ID
```

## 公开仓 Variables

```text
RELIEFWEB_APPNAME=wiv-virology-literature-tracker-42x
PUBLISHER_REPO=NailouZhang/pathogen-wechat-publisher
PIF_COVER_IMAGE_MODE=auto
PIF_LLM_REVIEW_MODE=balanced
PIF_PROFILE_RUNTIME_MINUTES=90
```

ReliefWeb 名称审核前，401/403 会标记 pending/skipped。

## 私有仓本地配置

微信密钥不放 GitHub Secrets，保存在：

```text
/home/stone/pathogen-wechat-publisher/runtime/config/publisher.env
```

字段：

```bash
WECHAT_APP_ID='...'
WECHAT_APP_SECRET='...'
ALLOWED_SOURCE_REPOS='NailouZhang/pathogen-intelligence-factory'
GITHUB_TRUST_ENV='false'
EXPECTED_PUBLIC_IP='159.226.127.153'
VERIFY_COVER_REMOTE='true'
WECHAT_AUTHOR=''
MAX_REPORT_AGE_HOURS='48'
```
