#!/usr/bin/env bash
set -euo pipefail

pull_check=0
if [[ "${1:-}" == "--pull-check" ]]; then
  pull_check=1
fi

section() {
  printf '\n== %s ==\n' "$1"
}

docker_output() {
  local tmp
  tmp="$(mktemp)"
  if "$@" >"$tmp" 2>&1; then
    cat "$tmp"
    rm -f "$tmp"
    return 0
  fi
  cat "$tmp"
  if grep -qi "permission denied.*docker" "$tmp"; then
    echo ""
    echo "permission fix:"
    echo "  sudo usermod -aG docker \"\$USER\""
    echo "  newgrp docker"
    echo "Then open a new WSL terminal and rerun this script."
  fi
  rm -f "$tmp"
  return 1
}

section "Docker Binary"
if ! command -v docker >/dev/null 2>&1; then
  echo "docker: not found"
  exit 1
fi
docker_path="$(command -v docker)"
echo "docker: $docker_path"
if [[ "$docker_path" == /mnt/c/* ]]; then
  echo "warning: docker points to Docker Desktop's Windows shim. For WSL-native Docker, expected /usr/bin/docker."
fi

section "Versions"
docker_output docker version --format 'Client={{.Client.Version}} Server={{.Server.Version}}' || true
docker_output docker compose version || true

section "Daemon"
docker_output docker info --format 'Server={{.ServerVersion}} Driver={{.Driver}} Cgroup={{.CgroupDriver}} Root={{.DockerRootDir}}' || true

section "Registry Mirrors"
docker_output docker info | sed -n '/Registry Mirrors/,+8p' || true

section "Project Volumes"
docker_output docker volume ls | grep -E 'one-stop-job|job_one_stop' || true

section "Compose Config"
docker compose config --quiet
echo "compose config: ok"

if [[ "$pull_check" == "1" ]]; then
  section "Pull Check"
  docker pull node:20-bookworm-slim
  docker pull python:3.12-slim
fi
