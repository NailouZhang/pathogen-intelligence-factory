# 公开仓运行手册 v13

固定仓库和路径：

```text
GitHub：NailouZhang/pathogen-intelligence-factory
本地：$HOME/github-projects/pathogen-intelligence-factory
Conda初始化：/home/stone/20T/DataBase/SoftwaresEnsembel/MiniConda/etc/profile.d/conda.sh
本地环境：$HOME/github-projects/pathogen-intelligence-factory/.conda-env
数据分支：intelligence-data
```

## 本地安装

```bash
cd "$HOME/github-projects/pathogen-intelligence-factory"
bash scripts/bootstrap_dev.sh
"$HOME/github-projects/pathogen-intelligence-factory/.conda-env/bin/python" -m playwright install --with-deps --only-shell chromium
bash scripts/doctor_local.sh
```

## 本地演示

```bash
cd "$HOME/github-projects/pathogen-intelligence-factory"
bash scripts/run_profile_local.sh hantavirus /tmp/pif-hantavirus-demo --demo
"$HOME/github-projects/pathogen-intelligence-factory/.conda-env/bin/python" scripts/issue_summary.py /tmp/pif-hantavirus-demo/data/latest.json
"$HOME/github-projects/pathogen-intelligence-factory/.conda-env/bin/python" scripts/validate_wechat_package.py /tmp/pif-hantavirus-demo/wechat-package
```

## GitHub手动运行

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  --ref main \
  -f profile_id=hantavirus \
  -f refresh_profile=false \
  -f cover_image_mode=deterministic \
  -f dispatch_wechat=false \
  -f review_mode=balanced
```

确认网页和审计文件后再触发公众号：

```bash
gh workflow run daily-intelligence.yml \
  --repo NailouZhang/pathogen-intelligence-factory \
  --ref main \
  -f profile_id=hantavirus \
  -f refresh_profile=false \
  -f cover_image_mode=auto \
  -f dispatch_wechat=true \
  -f review_mode=balanced
```

## 定时运行

工作流每天UTC 18:00触发。调度脚本按Asia/Shanghai确定星期，因此实际为北京时间次日02:00，并在同一Job中按配置顺序运行当天3个病毒。某个病毒失败时记录失败并继续运行队列中的下一个病毒；公众号派发仅对成功生成并校验的包执行。
