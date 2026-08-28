#!/usr/bin/env bash
# Install the pinned Bazecor build through the descriptor-safe helper next to
# this script. Installation remains an explicit action; the plugin never calls
# this entry point while loading or enabling.
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P) \
  || { echo "install-bazecor: cannot resolve the installer directory" >&2; exit 1; }

[[ -x /usr/bin/python3 ]] \
  || { echo "install-bazecor: /usr/bin/python3 is required" >&2; exit 1; }

exec /usr/bin/python3 "$SCRIPT_DIR/install-bazecor.py" "$@"
