# 权威页面发现策略已废弃

本版本不再使用 Google CSE、DuckDuckGo、ICTV 搜索页或 ViralZone 搜索页寻找权威页面。

固定页面维护位置：

```text
profiles/<profile_id>/seed.yaml
└── authoritative_sources
```

新增或替换 URL 后，首次运行使用 `refresh_profile=true`。抓取成功的正文会保存在数据分支的 profile 状态缓存中；页面暂时不可达时优先使用最近成功缓存。

仓库 Secrets 中可以删除：

```bash
gh secret delete GOOGLE_CSE_API_KEY --repo NailouZhang/pathogen-intelligence-factory
gh secret delete GOOGLE_CSE_ID --repo NailouZhang/pathogen-intelligence-factory
```
