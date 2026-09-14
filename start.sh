#!/bin/sh
set -eu
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
if command -v python3 >/dev/null 2>&1; then py=python3; elif command -v python >/dev/null 2>&1; then py=python; else echo '需要 Python 3.9+。' >&2; exit 1; fi
"$py" -c 'import sys;sys.exit(sys.version_info < (3,9))' || { echo '需要 Python 3.9+。' >&2; exit 1; }
exec "$py" "$app_dir/main.py" "$@"
