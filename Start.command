#!/bin/sh
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec /bin/sh "$app_dir/start.sh" "$@"
