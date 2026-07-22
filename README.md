# Pathogen Intelligence Factory 17.4.3

## v17.4-r3 运行重点

- LLM Provider 在每次新进程首次使用时先查询模型列表，再选择可用文本聊天模型；显式模型变量只在列表确认可用时优先。
- 模型列表不可用时保留兼容回退，避免模型发现接口故障中断全链路。
- 公开页面和微信公众号不展示复核范围、淘汰理由或“相关资料”等后台过程文案。
- 微信补充文献卡片与 Pages 中文信息结构一致，但不输出 DOI、PMID、PMCID 或来源链接。

私有主仓，负责21个Profile排班、Canonical词库、文献与新闻检索、日期门、候选复核、确定性与保守LLM去重、全文/元数据补全、终审、双语分析、静态站、微信发布包、审计和跨仓同步。

## v17.4-r3运行修复

- Factory CI会检出Pages和私有Publisher，并设置`PAGES_REPO_DIR`、`PUBLISHER_REPO_DIR`执行真实三仓契约测试。
- 统一由三仓包根目录的`system_manager.sh`执行配置、测试、GitHub任务、Pages和微信草稿操作。
- 页面输出会清除“审查得出的结论是”“范围说明”等后台处理措辞，但保留事实结论和补充资料身份。
- 翻译链为传统翻译器、批量结构化LLM、单字段结构化LLM、单字段纯文本LLM、英文可见兜底；单字段失败不终止整期生成。
- LLM路由支持Gemini、Groq、OpenRouter、Mistral、SiliconFlow、BigModel和DeepSeek的独立超时、模型后备、错误分类和最小兼容请求重试。
- 文献与新闻去重、相关性复核和终审继续采用现行保守证据保护规则，不降低目标病毒身份门槛。

## 本地验证

```bash
PYTHONPATH=src python -m pytest
PYTHONPATH=src python scripts/validate_all_profiles.py
PYTHONPATH=src python scripts/validate_canonical_vocabularies.py --project-root . --output /tmp/canonical.json
PYTHONPATH=src python scripts/audit_vocabulary_consumers.py --project-root . --output /tmp/consumers.json
PYTHONPATH=src python scripts/audit_pipeline_logic.py --project-root . --output /tmp/pipeline.json
PYTHONPATH=src python scripts/audit_query_coverage.py --project-root . --output /tmp/queries.json
```

跨仓pytest必须指向真实Pages与Publisher仓库：

```bash
PAGES_REPO_DIR=/path/to/pathogen-intelligence-pages \
PUBLISHER_REPO_DIR=/path/to/pathogen-wechat-publisher \
PYTHONPATH=src python -m pytest
```


### v17.4-r3动态模型发现与公开渲染

- 各LLM Provider优先调用模型列表接口，再按文本对话能力、稳定性和轻量模型优先级选择。
- GitHub Variables中的单模型名称只作为偏好；若列表中不存在，不会挡住已发现的可用模型。
- 公开Pages和微信公众号不再渲染“相关资料”等后台分类说明。
- 微信补充文献卡片与Pages中文卡片保持相同信息层级，并不输出DOI或来源链接。
