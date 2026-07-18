# 公开仓 GitHub 升级与运行命令 v8

```bash
set -Eeuo pipefail

cd "$HOME/下载"
chmod +x pathogen-weekly21-v8_public_manager.sh

bash pathogen-weekly21-v8_public_manager.sh extract
bash pathogen-weekly21-v8_public_manager.sh tag
bash pathogen-weekly21-v8_public_manager.sh sync
bash pathogen-weekly21-v8_public_manager.sh test
bash pathogen-weekly21-v8_public_manager.sh commit
bash pathogen-weekly21-v8_public_manager.sh configure-secrets
bash pathogen-weekly21-v8_public_manager.sh configure-vars

# 单病毒验收：不推微信、刷新词库、确定性封面、balanced 复核
bash pathogen-weekly21-v8_public_manager.sh   run-one hantavirus false true deterministic balanced

sleep 5
bash pathogen-weekly21-v8_public_manager.sh watch

# 日常重跑
bash pathogen-weekly21-v8_public_manager.sh   run-one hantavirus false false deterministic balanced

# 全部21个初始化，不推微信
bash pathogen-weekly21-v8_public_manager.sh   run-all false true deterministic balanced

# 当天3个并触发微信
bash pathogen-weekly21-v8_public_manager.sh   run-today true auto balanced

# 查看数据分支 SHA 和微信包
bash pathogen-weekly21-v8_public_manager.sh data-sha
bash pathogen-weekly21-v8_public_manager.sh check-package hantavirus
bash pathogen-weekly21-v8_public_manager.sh status
```
