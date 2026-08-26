#!/usr/bin/env bash
set -euo pipefail

# Release guard: credentials belong in the user's runtime environment, never
# in the frontend bundle or Tauri resources. Print variable names only.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

scan_paths=("$@")
if (($# == 0)); then
  scan_paths=(frontend/dist src-tauri/resources)
fi

for path in "${scan_paths[@]}"; do
  [[ -e "$path" ]] || continue
  if find "$path" -type f \( -name '.env' -o -name 'config.yaml' -o -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.db' \) -print -quit | grep -q .; then
    echo "ERROR: release payload contains a local secret/config/database file under $path" >&2
    exit 1
  fi
done

# Catch an accidental copy of the current machine's credentials even when a
# provider uses a non-standard token prefix. Never scan or log unrelated vars.
for name in $(env | sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' | grep -E '(_KEY|_TOKEN|_SECRET|_PASSWORD)$' || true); do
  value="${!name-}"
  [[ -n "$value" ]] || continue
  for path in "${scan_paths[@]}"; do
    [[ -e "$path" ]] || continue
    if grep -R -a -F -q --exclude='*.map' -- "$value" "$path" 2>/dev/null; then
      echo "ERROR: environment credential $name appears in release payload $path" >&2
      exit 1
    fi
  done
done

echo "ok: release payload contains no local secret/config files or matching runtime credentials"
