# ICTV/ViralZone 权威页面发现

优先级：

1. `seed.yaml` 中人工填写的 `authoritative_urls`；
2. 可选 Google Programmable Search JSON API；
3. 无密钥 DuckDuckGo HTML 搜索；
4. ICTV 和 ViralZone 站内搜索页回退。

可选 GitHub Secrets：

```bash
gh secret set GOOGLE_CSE_API_KEY --repo NailouZhang/pathogen-intelligence-factory
gh secret set GOOGLE_CSE_ID --repo NailouZhang/pathogen-intelligence-factory
```

没有这两个 Secret 时流程仍可运行。发现结果仅允许 `ictv.global` 和 `viralzone.expasy.org` 域名，并写入生成的严格 profile 的 `authoritative_sources` 中。

首次新增病原建议运行 `refresh_profile=true`。后续 profile、翻译词典和检索组从 `intelligence-data` 分支持久化复用。
