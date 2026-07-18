# 修复项 03：七要素/五要素分析可观测性与鲁棒兜底

## 目标

解决以下问题：

1. Gemini/Groq 大面积失败时，网页仍静默显示 `fallback_source_extract`，读者无法在顶部得知本期分析已严重降级。
2. `LLMResult.attempts` 只在成功结果中保存；失败后被压缩成一段字符串，无法区分密钥、限流、配额、超时、非法 JSON 或校验失败。
3. 旧分析缓存可能持续复用上一轮 fallback，掩盖服务恢复情况。
4. 确定性 fallback 只依赖少量角色关键词，摘要中已明确报告的方法和结果仍可能被输出为“未报告”。
5. 长全文证据可能使提示词过大，增加截断和非法 JSON 风险。

## 不可回退规则

1. 每次 LLM 尝试必须记录：任务、provider、model、时间、耗时、输入字符数、状态、失败类别和安全截断后的错误。
2. LLM 全部失败时，`LLMError` 必须携带结构化 `attempts`，不得只保留不可解析的字符串。
3. 失败类别至少区分：未配置、认证失败、限流、配额耗尽、超时、网络、服务不可用、上下文/输出超限、非法 JSON、结构校验失败和空响应。
4. 工作流运行前必须执行最小 JSON preflight；密钥值永远不得打印或写入审计。
5. fallback 比例超过 20% 时显示全局警告，超过 50% 时显示严重降级警告；阈值可配置。
6. 全局警告必须同时出现在 GitHub Actions 日志、GitHub Pages 顶部和微信公众号正文顶部。
7. `data/audit/analysis_quality.json` 必须保存候选池和最终展示记录的状态、比例、失败类别、provider/model 尝试及逐条 fallback 原因。
8. 分析策略版本升级后必须使旧缓存失效，不能继续复用修复前的 fallback。
9. fallback 必须先用扩展角色词、语义线索和摘要位置联合选句；方法和结果句必须优先保留，不能被宽泛的背景/设计字段抢占。
10. 只有证据确实不存在时才输出明确的证据缺失说明；不得用错误句子填充字段，也不得凭空推断。
11. 送入模型的证据超过字符预算时，必须按修辞角色保留代表句再压缩，并记录压缩前后字符数和证据条数。
12. 新闻、研究论文和综述均适用相同的全局质量统计和失败审计。

## 运行前检查

```bash
python scripts/check_credentials.py \
  --analysis-only \
  --probe-llm \
  --json-out /tmp/pif_llm_preflight.json
```

结果：

- `ready`：至少一个 provider 完成最小合法 JSON 请求；
- `failed`：配置了密钥，但请求、JSON 或校验失败；
- `unavailable`：Gemini/Groq 均未配置。

设置 `PIF_ANALYSIS_REQUIRE_LLM=true` 后，preflight 未通过会停止工作流；默认仅发出醒目警告并允许低置信 fallback，以免整条周报流水线无输出。

## 主要环境变量

```text
PIF_ANALYSIS_FALLBACK_WARNING_RATIO=0.20
PIF_ANALYSIS_FALLBACK_CRITICAL_RATIO=0.50
PIF_ANALYSIS_REQUIRE_LLM=false
PIF_LLM_MAX_MODELS_PER_PROVIDER=2
PIF_LLM_ATTEMPTS_PER_MODEL=1
PIF_LLM_MAX_OUTPUT_TOKENS=4096
PIF_ANALYSIS_MAX_PROMPT_CHARS=42000
PIF_LLM_HTTP_TIMEOUT=55
```

## 新增审计

`data/audit/analysis_quality.json` 包含：

- `displayed`：最终网页记录的通过/fallback 比例；
- `candidate_pool`：翻译与最终 Top-N 之前的分析质量；
- `top_failure_categories`：导致记录进入 fallback 的主因；
- `attempt_failure_categories`：所有 provider/model 尝试的失败分布；
- `provider_attempts`、`model_attempts`；
- `preflight`；
- `fallback_records`：逐条标题、ID、失败原因、fallback 策略和尝试明细。

## fallback 改进

研究论文采用以下优先分配顺序：

1. 方法；
2. 主要结果；
3. 设计与对象；
4. 问题与背景；
5. 解释与创新；
6. 科研与公卫意义；
7. 局限与证据强度。

这样可以避免“12名参与者阳性”被错误抢到设计字段，导致主要结果显示“未报告”。选句评分同时考虑：

- 显式小标题或角色；
- 方法/结果/设计/意义关键词；
- 数字、百分比和效应量；
- 句子在摘要中的相对位置；
- 跨字段不重复使用证据句。

## 主要改动文件

- `src/pifactory/llm.py`
- `src/pifactory/analysis.py`
- `src/pifactory/analysis_quality.py`
- `src/pifactory/http.py`
- `src/pifactory/config.py`
- `src/pifactory/pipeline.py`
- `src/pifactory/render.py`
- `scripts/check_credentials.py`
- `.github/workflows/daily-intelligence.yml`
- `tests/test_analysis_quality_v11.py`
