#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${COMMON_GROUND_PYTHON:-python3}" -B "$SCRIPT_DIR/launch.py" --config "$SCRIPT_DIR/config.json"
