#!/usr/bin/env bash
set -euo pipefail
cd /workspace/tally-automation
# Keep dependencies outside the checkout; preserve all project files.
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ is required"'
python3 -m venv /workspace/.venvs/tally-automation
/workspace/.venvs/tally-automation/bin/python -m pip install --disable-pip-version-check --index-url https://pypi.org/simple 'reportlab==4.4.9' 'pypdf==6.10.0' 'pillow==12.3.0' 'charset-normalizer==3.5.2'
/workspace/.venvs/tally-automation/bin/python -m pip check
