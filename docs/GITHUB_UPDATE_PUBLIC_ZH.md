# 公开仓 GitHub 升级命令 v9

```bash
cd "$HOME/下载"
chmod +x pathogen-weekly21-v9_public_manager.sh
bash pathogen-weekly21-v9_public_manager.sh extract
bash pathogen-weekly21-v9_public_manager.sh tag
bash pathogen-weekly21-v9_public_manager.sh sync
bash pathogen-weekly21-v9_public_manager.sh test
bash pathogen-weekly21-v9_public_manager.sh commit
bash pathogen-weekly21-v9_public_manager.sh configure-secrets
bash pathogen-weekly21-v9_public_manager.sh configure-vars

bash pathogen-weekly21-v9_public_manager.sh \
  run-one hantavirus false true deterministic balanced
sleep 5
bash pathogen-weekly21-v9_public_manager.sh watch

# 成功后日常运行
bash pathogen-weekly21-v9_public_manager.sh \
  run-one hantavirus false false deterministic balanced

# 全部21个，不推微信
bash pathogen-weekly21-v9_public_manager.sh \
  run-all false true deterministic balanced
```
