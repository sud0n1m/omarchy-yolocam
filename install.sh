#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
venv="$HOME/.local/share/omarchy-yolocam/venv"
omarchy plugin validate "$source_dir"
if [[ ! -x "$venv/bin/python" ]]; then
  uv venv --python '>=3.10' "$venv"
fi
uv pip install --python "$venv/bin/python" -r "$source_dir/requirements.txt"
omarchy-shell -q shell rescanPlugins
echo 'Dependencies installed. Enable with: omarchy plugin enable sudonim.yolocam --section right'
