# v6 凭据清单

## 公开仓 Secrets

- `CROSSREF_MAILTO`：建议；
- `NCBI_API_KEY`：强烈建议；
- `GEMINI_API_KEY`：完整分析所需；
- `GROQ_API_KEY`：文本回退；
- `OPENALEX_API_KEY`：OpenAlex 所需；
- `SEMANTIC_SCHOLAR_API_KEY`：可选，目前未获得也能匿名降速运行；
- `PUBLISHER_REPO_TOKEN`：触发私有发布仓所需。

## 公开仓 Variables

- `RELIEFWEB_APPNAME=wiv-virology-literature-tracker-42x`；
- `PUBLISHER_REPO=NailouZhang/pathogen-wechat-publisher`；
- `PIF_COVER_IMAGE_MODE=auto`。

ReliefWeb 审核前的未授权响应会记录为 pending/skipped。Semantic Scholar 无 Key 时执行全部编译查询，但缩小单请求结果并增加间隔。

## 私有仓本地配置

微信 AppID、AppSecret、白名单公网 IP、封面状态和发布状态只保存在 `/home/stone/pathogen-wechat-publisher/runtime/`，不进入 GitHub。
