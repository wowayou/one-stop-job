#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_URL="${APP_URL:-http://127.0.0.1:${HOST_PORT:-8000}}"

cd "$ROOT_DIR"

section() {
  printf '\n== %s ==\n' "$1"
}

fail() {
  echo "ERROR: $1"
  exit 1
}

warn() {
  echo "WARN: $1"
}

need_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "Missing command: $1"
  fi
}

section "Project Files"
for path in Dockerfile docker-compose.yml config.yaml frontend/package-lock.json requirements-runtime.txt; do
  [[ -e "$path" ]] || fail "Missing $path"
  echo "ok: $path"
done

section "Script Syntax"
bash -n scripts/deploy_check.sh
bash -n scripts/docker_doctor.sh
bash -n scripts/system_smoke.sh
echo "ok: shell scripts parse"

section "Config Guard"
if grep -RInE "(api_key|apikey|authorization|password|secret|token)[[:space:]]*:" config.yaml; then
  fail "config.yaml contains a sensitive-looking key. Move secrets to .env or environment variables."
fi
echo "ok: no sensitive key names found in config.yaml"

if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PY'
from pathlib import Path
import sys

try:
    import yaml
except Exception:
    sys.exit(0)

path = Path("config.yaml")
try:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
except Exception as exc:
    raise SystemExit(f"config.yaml YAML parse failed: {exc}") from exc
if not isinstance(loaded, dict):
    raise SystemExit("config.yaml root must be an object")
PY
  echo "ok: config.yaml parses"
else
  warn "python3 not found; skipped YAML parse check"
fi

section "Docker Compose"
need_command docker
docker compose config --quiet
echo "ok: docker compose config"

section "Runtime Probe"
if curl -fsS --max-time 2 "$APP_URL/api/health" >/dev/null 2>&1; then
  echo "ok: $APP_URL/api/health"
  if curl -fsS --max-time 5 "$APP_URL/api/ready"; then
    echo ""
    echo "ok: $APP_URL/api/ready"
  else
    warn "$APP_URL/api/ready reported a problem. Review the JSON above or run: docker compose logs app"
  fi
else
  warn "App is not responding at $APP_URL. Start it with: docker compose up -d --build"
fi

section "Deploy Check Complete"
