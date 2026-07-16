# Pathogen Intelligence Factory

公开内容仓库：只输入一个或少数病原词，自动构建严格术语档案，检索近 7 天学术文献与新闻，完成证据化五要素分析、独立翻译、网页排版、病原封面和微信公众号标准发布包。

## 两仓架构

```text
pathogen-intelligence-factory (public)
  ├─ profiles/<profile_id>/seed.yaml
  ├─ strict terminology bootstrap
  ├─ PubMed / Europe PMC / Crossref / Semantic Scholar / OpenAlex / bioRxiv-medRxiv
  ├─ Google News / Bing / GDELT / ReliefWeb / WHO
  ├─ translation + evidence-bound five elements
  ├─ GitHub Pages
  └─ wechat-package v2
           ↓ repository_dispatch
pathogen-wechat-publisher (private, self-hosted Ubuntu)
  └─ cover SHA/media_id + draft/add
```

## 最小病原输入

```yaml
profile_id: hantavirus
seed_terms:
  - hantavirus
  - Orthohantavirus
display_name_en: Hantavirus
display_name_zh: 汉坦病毒
authoritative_urls:
  - https://ictv.global/report/chapter/hantaviridae/hantaviridae
  - https://viralzone.expasy.org/213
negative_terms: [fictional, game, stock]
```

首次真实运行会从权威页面建立严格双语 profile；只有 seed 或强制刷新发生变化时才重建。

## 新增病原

```bash
python scripts/create_profile.py \
  --profile-id nipah-virus \
  --term "Nipah virus" \
  --term "Henipavirus nipahense" \
  --name-en "Nipah virus" \
  --name-zh "尼帕病毒" \
  --url "https://ictv.global/..." \
  --url "https://viralzone.expasy.org/..."
```

然后：

```bash
git add profiles/nipah-virus/seed.yaml
git commit -m "feat: add Nipah virus profile"
git push
gh workflow run daily-intelligence.yml -f profile_id=nipah-virus -f refresh_profile=true
```

## 页面结构

- 深蓝封面与标题区；
- 米黄色“今日核心综述”；
- 绿色学术文献卡；
- 红色新闻卡；
- 文献元数据条展示期刊、在线发表、首次发表、印刷日期、数据库创建日期、当前可报道日期、DOI、卷期页；
- 中文标题下第一段始终是原始摘要的中文翻译；
- 新闻标题下第一段始终是抓获正文/简要的中文翻译；
- 五要素独立展示，不能替代原始摘要或正文翻译；
- GitHub Pages 支持中英文切换；微信公众号 HTML 使用无 JavaScript 的稳定排版。

## 封面

优先级：

1. `profiles/<profile_id>/cover_override.jpg`；
2. Gemini 图片模型生成无文字病原插图；
3. Pillow 确定性科学插图回退。

profile 指纹变化时才重新生成。最终封面保持固定深蓝、绿色和克制红色的统一视觉风格，并输出到 `wechat-package/cover.jpg`。

## 本地 demo

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/run_daily.py --profile hantavirus --output-dir /tmp/pif-demo --state-dir /tmp/pif-demo/data/state --demo
python scripts/validate_wechat_package.py /tmp/pif-demo/wechat-package
```

## GitHub Secrets

```bash
gh secret set CROSSREF_MAILTO
gh secret set NCBI_API_KEY
gh secret set GEMINI_API_KEY
gh secret set GROQ_API_KEY
gh secret set SEMANTIC_SCHOLAR_API_KEY       # optional
gh secret set PUBLISHER_REPO_TOKEN
```

Variables:

```bash
gh variable set PIF_PROFILE_ID --body hantavirus
gh variable set PUBLISHER_REPO --body NailouZhang/pathogen-wechat-publisher
gh variable set GEMINI_IMAGE_MODEL --body gemini-3.1-flash-image
gh variable set PIF_COVER_IMAGE_MODE --body auto
```

完整安装见 `docs/INSTALL_ZH.md`。
