# 公开仓 GitHub 升级命令 v10

```bash
cd "$HOME/下载"
chmod +x pathogen-weekly21-v10_public_manager.sh
bash pathogen-weekly21-v10_public_manager.sh extract
bash pathogen-weekly21-v10_public_manager.sh tag
bash pathogen-weekly21-v10_public_manager.sh sync
bash pathogen-weekly21-v10_public_manager.sh install-browser
bash pathogen-weekly21-v10_public_manager.sh test
bash pathogen-weekly21-v10_public_manager.sh commit
bash pathogen-weekly21-v10_public_manager.sh configure-vars
```

测试汉坦病毒：

```bash
bash pathogen-weekly21-v10_public_manager.sh run-one hantavirus false false deterministic balanced
sleep 5
bash pathogen-weekly21-v10_public_manager.sh watch
```

确认后推微信：

```bash
bash pathogen-weekly21-v10_public_manager.sh run-one hantavirus true false auto balanced
```
