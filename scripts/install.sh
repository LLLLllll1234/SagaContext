#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "${PYTHON:-python3}" "$ROOT/scripts/sagacontext_setup.py" --root "$ROOT" "$@"
