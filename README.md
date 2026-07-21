# Pathogen Intelligence Factory v17.2

这是三仓系统中的私有生产仓，负责21种病原的文献与新闻召回、权威日期门禁、去重、全文/正文补全、相关性终审、双语结构化分析、公开静态站生成、微信公众号不可变发布包和完整后台审计。

v17.2保持21个Profile ID、5个冻结核心概念、受控补充查询、词库bundle `2026.07-v17.1`、Top50/补充目录规则和输出Schema不变，重点修复：

1. 候选数达到10且终审通过率低于30%时启动异常复核；30%仅为触发线，安全恢复目标仍独立为15%，硬身份冲突永不恢复。
2. 每条新闻终审拒绝均记录原因；正文失败仍只进入补充目录。
3. 合法日文、韩文、西里尔文等原始标题以明确元数据身份渲染，不再被误判为补充卡正文。
4. LLM输出先标准化、去重和语言清理，再严格校验；只重写失败字段，仍失败才进入保守规则兜底。
5. Pages和微信公众号包采用正常渲染、确定性修复、元数据安全回退、应急元数据输出四层连续性保护。

## 固定本地目录

```text
仓库：/home/stone/github-projects/pathogen-intelligence-factory
Conda：/home/stone/github-projects/pathogen-intelligence-factory/.conda-env
```

## 安装与测试

```bash
TOOLS=/home/stone/pathogen-wechat-publisher/releases/current
bash "$TOOLS/install_three_repos.sh" install --run-tests

cd /home/stone/github-projects/pathogen-intelligence-factory
PYTHONPATH=src ./.conda-env/bin/python -m pytest -o addopts='' -q
bash scripts/doctor_local.sh
```

## 运行

```bash
TOOLS=/home/stone/pathogen-wechat-publisher/releases/current
bash "$TOOLS/system_manager.sh" run-one respiratory_syncytial_virus false
bash "$TOOLS/system_manager.sh" run-today true
bash "$TOOLS/factory_manager.sh" publish-pages
```

完整配置和业务逻辑见工程包根目录：

```text
INSTALL_CONFIG_RUN_V17_2_ZH.md
ARCHITECTURE_AND_RUN_LOGIC_V17_2_ZH.md
V17_2_NEWS_RENDER_LLM_RELIABILITY_ZH.md
```
