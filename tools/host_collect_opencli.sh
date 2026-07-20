#!/usr/bin/env bash
set -euo pipefail

SOURCE="${1:-boss}"
API_BASE="${2:-http://127.0.0.1:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "$SCRIPT_DIR/host_opencli_import.py" --source "$SOURCE" --api "$API_BASE"
