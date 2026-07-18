# v8 本地安装与验证

## 公开仓

```bash
cd "$HOME/github-projects/pathogen-intelligence-factory"
python -m pip install -r requirements.txt
python scripts/validate_all_profiles.py
python scripts/audit_query_coverage.py --output /tmp/query-coverage-v8.json
python scripts/check_credentials.py || true
python -m pytest -q
python -m compileall -q src scripts tests
```

单病毒 Demo：

```bash
python scripts/run_daily.py   --profile hantavirus   --output-dir /tmp/pif-v8-demo   --state-dir /tmp/pif-v8-demo/data/state   --demo
```

## 私有仓

```bash
cd "$HOME/pathogen-wechat-publisher/repository"
bash scripts/bootstrap_local.sh
PYTHON="$HOME/pathogen-wechat-publisher/conda-env/bin/python"
export PYTHONPATH="$PWD/src"
"$PYTHON" -m pytest -q
"$PYTHON" -m wechat_publisher.cli doctor
```

生产任务直接调用固定解释器，不依赖 `conda activate`。
