# v15.3 独立仓测试隔离与新闻来源状态修复

## 修复范围

本版本不改变五词检索、文献生命周期、LLM路由、Pages结构或 `pathogen-wechat-package/v2` 跨仓协议。修复集中在独立公开仓测试可移植性和新闻正文来源状态。

## 独立公开仓测试

公开仓的pytest不得依赖两仓ZIP外层的 `public_manager.sh`。`refresh_profile=false` 默认值改为直接检查公开仓内的 `daily-intelligence.yml`；根目录管理器仍由 `validate_bundle.sh` 单独验证。

## 测试网络隔离

`tests/conftest.py` 默认设置 `PIF_NEWS_BROWSER_ENABLED=false`。需要验证Playwright的测试必须显式设置为true，并通过monkeypatch注入HTML。这样测试不会因本机安装Chromium、网络可达性或Google页面变化而漂移。

## 新闻来源状态

Google News和Bing News聚合页属于发现入口。只有渲染后最终URL或canonical URL解析到非聚合出版商地址时，抽取正文才可标记为 `full` 或 `partial`。否则聚合页内容记录 `unresolved_aggregator_landing` 并拒绝升级，随后使用RSS来源摘要：

- 实质性摘要：`syndicated_summary`；
- 仅重复标题：`title_only_rejected`；
- 身份不符：`identity_rejected`；
- 无内容：`unavailable`。

## 跨仓兼容

公众号包仍为 `pathogen-wechat-package/v2`，Runner目录、标签、dispatch事件及 `publisher.env` 均不变。
