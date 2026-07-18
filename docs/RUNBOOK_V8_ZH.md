# weekly21 v8 完整运行逻辑

## 阶段 1：词库与检索概念

每个 profile 保留约 5 个有区分度的核心检索概念。完整专业词库主要用于检索后核验，不将大量近义词全部提交给数据库。

## 阶段 2：多源元数据发现

学术来源：PubMed、Europe PMC、Crossref、Semantic Scholar、OpenAlex、bioRxiv、medRxiv。  
新闻来源：Google News RSS、Bing News RSS、GDELT、ReliefWeb、WHO。

## 阶段 3：全候选轻处理

```text
时间窗口
→ Python 身份和上下文检查
→ DOI/PMID/标题/URL 去重
→ 多来源与多概念命中合并
→ balanced 模式仅把歧义记录交给 LLM
```

## 阶段 4：展示候选

按相关性、研究设计、摘要质量、来源收敛度、热点和新近性排序。先建立 Top 50，再保留最多 20 条补位候选；补位候选只在前项缺摘要/全文或新闻正文失败时使用。

## 阶段 5：内容补全

文献只使用合法开放来源或数据库摘要；新闻尝试解析真实报道 URL，并依次使用 JSON-LD、Trafilatura 精确模式、召回模式、正文区域和可见段落提取。标题重复、正文过短或导航噪声会被拒绝。

## 阶段 6：单篇结构化分析

- 原始研究：七要素。
- 综述/Meta 分析/观点：五要素。
- 新闻/官方通报：五要素和 55～170 词英文简报。

每次调用只处理一条记录；输入带编号证据句；输出必须为固定 JSON；验证失败会重试或进入确定性兜底。

## 阶段 7：翻译

```text
deep-translator Google
→ Python 直接 Google 翻译入口
→ MyMemory
→ Gemini/Groq 最终兜底
→ 质量门禁
```

不使用 Google Cloud Translation。标题、摘要/新闻简报和每个分析字段都必须成功；失败项不以“翻译暂不可用”占位符进入页面。

## 阶段 8：综合汇总

文献和新闻分别选取 15～25 条：

- 文献汇总输入作者、期刊、时间、摘要、文章类型、质量等级和单篇分析。
- 新闻汇总只输入正文验证成功的新闻，按同一事件合并并区分已确认与不确定信息。

## 阶段 9：展示与发布

生成双语 GitHub Pages、`latest.json`、审计文件和 `wechat-package/v2`。私有仓本地 Runner 校验并创建草稿，不自动群发。


# weekly21 v8 双仓系统完整安装说明

## 1. 交付目标

v8 完成以下链路：

```text
5 个核心检索概念/病毒
→ 学术文献与新闻元数据检索
→ Python 全候选相关性复核与去重
→ LLM 仅处理边界相关性记录
→ 按相关性、研究质量、热点和来源收敛度建立展示候选队列
→ Top 50 + 最多 20 条补位候选进行合法内容补全
→ 选出最多 50 篇有证据文献与 50 条有正文新闻
→ 研究论文七要素、综述五要素、新闻五要素逐篇分析
→ 免费 Python 翻译优先，LLM 最终兜底
→ 文献与新闻分别选取 15～25 条做综合汇总
→ GitHub Pages
→ wechat-package/v2
→ 私有仓本地 Runner
→ 微信公众号草稿箱
```

公开仓：`NailouZhang/pathogen-intelligence-factory`  
私有仓：`NailouZhang/pathogen-wechat-publisher`

## 2. 默认路径

```text
ZIP：$HOME/下载/pathogen-weekly21-v8-complete-bundle.zip
公开仓：$HOME/github-projects/pathogen-intelligence-factory
私有仓：$HOME/pathogen-wechat-publisher/repository
私有系统根目录：$HOME/pathogen-wechat-publisher
解压目录：/tmp/pathogen-weekly21-v8-bundle
```

ZIP 位于其他位置时：

```bash
export BUNDLE_ZIP='/实际路径/pathogen-weekly21-v8-complete-bundle.zip'
```

## 3. 基础工具

```bash
sudo apt-get update
sudo apt-get install -y git unzip rsync curl

gh --version
gh auth status
ssh -T git@github.com
```

未登录 GitHub CLI 时：

```bash
gh auth login
```

## 4. 准备管理脚本

```bash
cd "$HOME/下载"
chmod +x   pathogen-weekly21-v8_public_manager.sh   pathogen-weekly21-v8_private_manager.sh
```

## 5. 公开仓升级

```bash
cd "$HOME/下载"

bash pathogen-weekly21-v8_public_manager.sh extract
bash pathogen-weekly21-v8_public_manager.sh tag
bash pathogen-weekly21-v8_public_manager.sh sync
bash pathogen-weekly21-v8_public_manager.sh test
bash pathogen-weekly21-v8_public_manager.sh commit
```

`sync` 使用 `rsync --delete`，同步前要求 Git 工作区干净，并应先执行 `tag`。

## 6. 公开仓 Secrets

```bash
bash "$HOME/下载/pathogen-weekly21-v8_public_manager.sh" configure-secrets
```

交互设置：

```text
CROSSREF_MAILTO               必填
UNPAYWALL_EMAIL               可选；为空时使用 CROSSREF_MAILTO
NCBI_API_KEY                  必填
GEMINI_API_KEY                必填
GROQ_API_KEY                  必填
OPENALEX_API_KEY              必填
SEMANTIC_SCHOLAR_API_KEY      可选；目前没有可跳过
PUBLISHER_REPO_TOKEN          必填；用于触发私有发布仓
```

v8 不使用 Google Cloud Translation，也不需要 Google Cloud 计费凭据。翻译链使用免费的 Python 翻译入口，全部失败后才使用现有 Gemini/Groq。

## 7. 公开仓 Variables

```bash
bash "$HOME/下载/pathogen-weekly21-v8_public_manager.sh" configure-vars
```

设置：

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

## 8. GitHub Pages

仓库网页中设置：

```text
Settings → Pages → Build and deployment → Source → GitHub Actions
```

工作流使用 `upload-pages-artifact` 和 `deploy-pages` 发布多 profile 门户。

## 9. 首次测试

先运行汉坦病毒，不触发微信，不调用图片模型：

```bash
bash "$HOME/下载/pathogen-weekly21-v8_public_manager.sh"   run-one hantavirus false true deterministic balanced

sleep 5

bash "$HOME/下载/pathogen-weekly21-v8_public_manager.sh" watch
```

首次成功后，日常重跑使用：

```bash
bash "$HOME/下载/pathogen-weekly21-v8_public_manager.sh"   run-one hantavirus false false deterministic balanced
```

初始化全部 21 个 profile：

```bash
bash "$HOME/下载/pathogen-weekly21-v8_public_manager.sh"   run-all false true deterministic balanced
```

## 10. 私有仓升级

```bash
cd "$HOME/下载"

bash pathogen-weekly21-v8_private_manager.sh tag
bash pathogen-weekly21-v8_private_manager.sh sync
bash pathogen-weekly21-v8_private_manager.sh bootstrap
bash pathogen-weekly21-v8_private_manager.sh test
bash pathogen-weekly21-v8_private_manager.sh commit
```

已有本地微信配置时不重新运行 `configure-local`。首次配置：

```bash
bash "$HOME/下载/pathogen-weekly21-v8_private_manager.sh" configure-local
```

本地密钥文件：

```text
$HOME/pathogen-wechat-publisher/runtime/config/publisher.env
```

## 11. Runner

已有 Runner：

```bash
bash "$HOME/下载/pathogen-weekly21-v8_private_manager.sh" restart-runner
bash "$HOME/下载/pathogen-weekly21-v8_private_manager.sh" runner-status
```

首次注册：

```bash
bash "$HOME/下载/pathogen-weekly21-v8_private_manager.sh" setup-runner
sudo loginctl enable-linger "$USER"
```

## 12. 草稿测试

```bash
bash "$HOME/下载/pathogen-weekly21-v8_private_manager.sh"   check-package hantavirus

bash "$HOME/下载/pathogen-weekly21-v8_private_manager.sh"   draft hantavirus true false

sleep 5

bash "$HOME/下载/pathogen-weekly21-v8_private_manager.sh" watch
```

`force=true` 允许重新生成同一天草稿；`refresh_cover=false` 在图片未变化时复用永久封面素材。

## 13. 自动调度

| 星期（北京时间） | profile 1 | profile 2 | profile 3 |
|---|---|---|---|
| 周一 | seasonal_influenza | sars_cov_2 | respiratory_syncytial_virus |
| 周二 | human_metapneumovirus | human_adenovirus | human_enterovirus |
| 周三 | norovirus | measles_virus | human_papillomavirus |
| 周四 | dengue_virus | chikungunya_virus | avian_influenza |
| 周五 | hantavirus | sftsv | mpox_virus |
| 周六 | nipah_virus | arenaviridae | ebola_viruses |
| 周日 | marburg_virus | rabies_virus | hepatitis_b_virus |

工作流 cron 为 `0 18 * * *`，解析器使用 `Asia/Shanghai`，对应北京时间 02:00。

## 14. 日常状态检查

```bash
bash "$HOME/下载/pathogen-weekly21-v8_public_manager.sh" status
bash "$HOME/下载/pathogen-weekly21-v8_private_manager.sh" status
```

## 15. 回滚

```bash
cd "$HOME/github-projects/pathogen-intelligence-factory"
git log --oneline --decorate -n 15
git revert <v8提交SHA>
git push
```

私有仓采用相同方式。升级前标签格式：

```text
before-weekly21-v8-YYYYMMDD-HHMMSS
```
