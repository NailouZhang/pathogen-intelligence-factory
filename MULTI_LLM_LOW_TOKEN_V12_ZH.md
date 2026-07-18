# 修复/增强项04：多 LLM 免费额度轮换与低 Token 文献精读

## 目标

在修复项01—03的基础上，将 Gemini、Groq、OpenRouter、Mistral 和 SiliconFlow 统一纳入同一结构化分析路由；当一个模型或供应商限流、余额不足或不可用时自动切换，同时禁止把整篇全文直接发送给远程LLM。

## 不可回退原则

1. 全文只在本地解析和检索证据，任何远程LLM最多接收经过本地筛选的短证据包。
2. 所有文献至少可做摘要级 L1 分析；只有排名靠前的有限文献进入 L2 全文证据增强；只有极少数重要文献进入 L3 第二供应商复核。
3. 成功通过 validator 的结果才写入持久分析缓存；fallback 或失败结果默认不得长期缓存。
4. 402/明确余额不足视为配额耗尽并切换供应商；429视为短期冷却，不能直接等同于永久额度耗尽。
5. 每个平台、每个模型分别维护请求数、成功数、失败数、输入/输出Token和冷却状态。
6. API密钥只能通过环境变量或GitHub Secrets提供，任何日志和审计文件不得包含密钥。
7. 模型名称不得写死为唯一选择；优先读取GitHub Variables/环境变量，再调用模型列表接口发现当前可用模型。
8. 总览只使用已经验证的结构化分析结果，不再重复输入原始全文。

## 五个供应商

- Gemini：原生 `generateContent` 适配器；
- Groq：OpenAI兼容适配器；
- OpenRouter：OpenAI兼容适配器，默认 `openrouter/free`，并查询 `/api/v1/key` 的安全额度信息；
- Mistral：OpenAI兼容适配器，默认 `mistral-small-latest`；
- SiliconFlow：OpenAI兼容适配器，支持关闭thinking，并查询 `/v1/user/info` 余额。

## 默认任务池

```text
抽取池：siliconflow,groq,mistral,openrouter,gemini
救援池：gemini,mistral,openrouter,groq,siliconflow
总览池：gemini,mistral,openrouter,groq,siliconflow
相关性池：groq,siliconflow,mistral,gemini,openrouter
```

顺序均可通过GitHub Variables修改，不需要重新改代码。

## 低 Token 三层分析

### L1：摘要基础精读

- 默认用于除Top-N以外的全部文献；
- 只发送标题、书目信息和摘要证据句；
- 即使本地已经获取全文，也不允许全文内容进入远程请求。

### L2：全文关键证据增强

- 默认只用于排名前12篇；
- 本地按章节、角色、BM25式词权重、数字结果、方法术语和去重筛选证据；
- 默认最多发送9000字符的证据包；
- 方法、结果、设计、局限、结论等角色至少保留代表证据。

### L3：跨供应商复核

- 默认只用于排名前5篇；
- 第一模型成功后，从救援池中排除第一供应商，再调用一个独立供应商；
- 计算字段间一致度；明显不一致时降低置信度并保留交叉核验审计。

## 供应商状态机

```text
healthy
cooldown
rate_limited
quota_exhausted
authentication_failed
provider_unavailable
disabled
```

- 401/403：认证或权限失败，本次运行停用该供应商；
- 402/余额不足：本次运行停用该供应商；
- 429：当前模型进入冷却，继续尝试同平台其他模型或下一供应商；
- 5xx/超时/网络错误：供应商短期冷却；
- JSON/validator失败：换模型或供应商，不盲目重复同一模型。

## 缓存

缓存键由以下内容构成：

```text
文献稳定身份 + 本地证据包内容 + 分析策略版本 + Schema
```

默认只缓存 `status=passed` 的结果。修复项04将策略版本升级为：

```text
v12-multillm-low-token-analysis-1
```

因此修复项03之前的分析缓存会自动失效。

## 新增审计

```text
data/audit/llm_provider_usage.json
```

记录：

- 各任务池顺序；
- 每个供应商是否配置；
- 状态与冷却；
- 每个模型请求/成功/失败次数；
- 输入、输出和总Token；
- OpenRouter额度快照；
- SiliconFlow余额快照。

单篇文献分析还记录：

- `analysis_level`；
- `evidence_scope`；
- `evidence_selector`；
- L3 `crosscheck` 状态和一致度。

## Secrets

```bash
gh secret set GEMINI_API_KEY
gh secret set GROQ_API_KEY
gh secret set OPENROUTER_API_KEY
gh secret set MISTRAL_API_KEY
gh secret set SILICONFLOW_API_KEY
```

## 推荐 Variables

```bash
gh variable set PIF_ANALYSIS_FULLTEXT_TOP_N --body '12'
gh variable set PIF_ANALYSIS_CROSSCHECK_TOP_N --body '5'
gh variable set PIF_ANALYSIS_EVIDENCE_MAX_CHARS --body '9000'
gh variable set PIF_ANALYSIS_MAX_PROMPT_CHARS --body '14000'
gh variable set PIF_LLM_MAX_OUTPUT_TOKENS --body '1400'
gh variable set PIF_LLM_CACHE_SUCCESS_ONLY --body 'true'
gh variable set PIF_LLM_DISABLE_THINKING --body 'true'
gh variable set PIF_LLM_PROVIDER_COOLDOWN_SECONDS --body '60'
gh variable set PIF_LLM_EXTRACT_PROVIDER_ORDER --body 'siliconflow,groq,mistral,openrouter,gemini'
gh variable set PIF_LLM_RESCUE_PROVIDER_ORDER --body 'gemini,mistral,openrouter,groq,siliconflow'
gh variable set PIF_OPENROUTER_FREE_ONLY --body 'true'
```

## 运行前检查

```bash
python scripts/check_credentials.py \
  --analysis-only \
  --probe-llm \
  --json-out /tmp/pif_llm_preflight.json
```

脚本会检查五个供应商，并在平台支持时读取安全额度/余额摘要，不输出任何API Key。
