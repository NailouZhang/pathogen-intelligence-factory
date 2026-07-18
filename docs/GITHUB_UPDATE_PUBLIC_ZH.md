# v7 公开仓 GitHub 更新命令

以下命令只修改公开仓。

```bash
rm -rf /tmp/pathogen-weekly21-v7-bundle
unzip "$HOME/下载/pathogen-weekly21-v7-complete-bundle.zip" -d /tmp

cd "$HOME/github-projects/pathogen-intelligence-factory"
git status

git tag -a "before-weekly21-v7-$(date +%Y%m%d-%H%M%S)"   -m "Stable public version before weekly21 v7"
git push origin --tags

PUBLIC_REPO_DIR="$HOME/github-projects/pathogen-intelligence-factory"   bash /tmp/pathogen-weekly21-v7-bundle/install_public_repo_update.sh

cd "$HOME/github-projects/pathogen-intelligence-factory"
python -m pip install -r requirements.txt
python scripts/validate_all_profiles.py
python scripts/audit_query_coverage.py
python -m pytest -q
python -m compileall -q src scripts tests

git status
git diff --stat
git add .
git commit -m "feat: use lean five-concept retrieval and Top-50 enrichment v7"
git push
```
