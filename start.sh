#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONUTF8=1
args=()
setup=false
for arg in "$@"; do
    if [[ "$arg" == '--setup' ]]; then setup=true; else args+=("$arg"); fi
done
if $setup; then
    "${PYTHON:-python3}" -X utf8 "$ROOT/scripts/portable/setup.py"
fi
if [[ ! -f "$ROOT/.venv/bin/python" ]]; then
    echo 'Run bash start.sh --setup first (Python 3.14+, Node/npm and Git required).' >&2
    exit 1
fi
# macOS Bash 3.2 treats an empty array as unset under `set -u`.
exec "$ROOT/.venv/bin/python" -X utf8 "$ROOT/scripts/start.py" ${args[@]+"${args[@]}"}
