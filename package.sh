#!/usr/bin/env bash
# Builds code.zip exactly as the starter README prescribes (no dataset/, venvs, caches or nested archives)
# and mirrors the reports to ./evaluation/ and ./docs/. Portable alternative: python3 code/tools/package.py
set -euo pipefail
cd "$(dirname "$0")"
python3 code/main.py
python3 code/evaluation/validate_output.py
python3 code/evaluation/main.py --reports >/dev/null
python3 code/evaluation/ceiling_analysis.py >/dev/null
rm -rf evaluation docs && mkdir evaluation docs && cp code/evaluation/*.md evaluation/ && cp code/docs/*.md docs/
find code -name __pycache__ -type d -prune -exec rm -rf {} +
rm -f code.zip
if command -v zip >/dev/null 2>&1; then
  zip -r code.zip README.md Makefile package.sh LICENSE CHANGELOG.md pyproject.toml docs evaluation/evaluation_report.md evaluation/usage_report.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store" >/dev/null
else
  python3 code/tools/package.py
fi
echo "code.zip: $(unzip -Z1 code.zip | wc -l) entries"
if unzip -Z1 code.zip | grep -Eq '\.(zip|tar|gz|tgz|7z|rar)$|^dataset/|__pycache__'; then echo "ERROR: forbidden entry in code.zip"; exit 1; fi
