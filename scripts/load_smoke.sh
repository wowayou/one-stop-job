#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT_DIR/.venv/bin/python"
HOST="127.0.0.1"
JOBS="${JOBS:-300}"
CONCURRENCY="${CONCURRENCY:-8}"

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing .venv/bin/python."
  exit 1
fi

tmp_dir="$(mktemp -d)"
backend_pid=""

cleanup() {
  if [[ -n "${backend_pid:-}" ]] && kill -0 "$backend_pid" 2>/dev/null; then
    kill "$backend_pid" 2>/dev/null || true
    wait "$backend_pid" 2>/dev/null || true
  fi
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

PORT="$("$PYTHON" - <<'PY'
import socket

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PY
)"

db_path="$tmp_dir/load-smoke.sqlite3"
config_path="$tmp_dir/config.yaml"
backend_log="$tmp_dir/backend.log"
base_url="http://$HOST:$PORT"
cp "$ROOT_DIR/config.yaml" "$config_path"

(
  cd "$ROOT_DIR"
  OPENAI_API_KEY="" OPENAI_BASE_URL="" JOB_ONE_STOP_CONFIG="$config_path" JOB_ONE_STOP_DATABASE_URL="sqlite:///$db_path" "$PYTHON" -m uvicorn backend.app.main:app --host "$HOST" --port "$PORT" >"$backend_log" 2>&1 &
  echo "$!" >"$tmp_dir/backend.pid"
)
backend_pid="$(cat "$tmp_dir/backend.pid")"

"$PYTHON" - "$base_url" "$backend_log" <<'PY'
import sys
import time
import urllib.error
import urllib.request

base_url, log_path = sys.argv[1], sys.argv[2]
for _ in range(80):
    try:
        with urllib.request.urlopen(base_url + "/api/health", timeout=1) as response:
            if response.status == 200:
                break
    except (OSError, urllib.error.URLError):
        time.sleep(0.25)
else:
    print("Backend did not become healthy. Recent log:", file=sys.stderr)
    try:
        print(open(log_path, encoding="utf-8").read()[-4000:], file=sys.stderr)
    except OSError:
        pass
    raise SystemExit(1)
PY

BASE_URL="$base_url" JOBS="$JOBS" CONCURRENCY="$CONCURRENCY" "$PYTHON" - <<'PY'
from __future__ import annotations

import csv
import io
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx


BASE_URL = os.environ["BASE_URL"]
JOBS = int(os.environ["JOBS"])
CONCURRENCY = int(os.environ["CONCURRENCY"])


def assert_ok(response: httpx.Response, label: str) -> httpx.Response:
    if response.status_code >= 400:
        raise AssertionError(f"{label} failed: {response.status_code} {response.text[:500]}")
    return response


def timed(label: str, fn):
    started = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - started
    print(f"{label}: {elapsed:.3f}s")
    return result, elapsed


csv_buffer = io.StringIO()
writer = csv.writer(csv_buffer)
writer.writerow(["title", "company", "salary", "area", "skills", "description"])
for index in range(JOBS):
    title = "SEO运营" if index % 2 else "外贸SEO运营"
    company = f"压力测试公司{index % 80:03d}"
    salary = f"{8 + index % 5}-{12 + index % 7}K"
    area = "示例市·中心区" if index % 3 else "示例市·北区"
    skills = "SEO,数据分析,内容运营" if index % 2 else "SEO,外贸运营,Google Analytics"
    writer.writerow([title, company, salary, area, skills, f"压力测试岗位 {index}"])
csv_bytes = csv_buffer.getvalue().encode("utf-8")

with httpx.Client(base_url=BASE_URL, timeout=30) as client:
    assert_ok(client.get("/api/health"), "health")

    imported, import_elapsed = timed(
        f"import {JOBS} jobs",
        lambda: assert_ok(
            client.post(
                "/api/jobs/import",
                params={"source": "压力测试"},
                files={"file": ("load.csv", csv_bytes, "text/csv")},
            ),
            "import",
        ).json(),
    )
    if imported["fetched"] != JOBS:
        raise AssertionError(f"expected {JOBS} fetched, got {imported}")

    jobs_payload, list_elapsed = timed("list jobs", lambda: assert_ok(client.get("/api/jobs"), "list jobs").json())
    if len(jobs_payload) < min(JOBS, 80):
        raise AssertionError(f"unexpectedly few jobs after canonical merge: {len(jobs_payload)}")

    profile_payload = {
        "target_titles": "SEO,运营,外贸SEO",
        "target_cities": "示例市,中心区,北区",
        "salary_min_k": 7,
        "salary_max_k": 16,
        "skills": "SEO,数据分析,内容运营,Google Analytics",
        "dealbreakers": "单休,纯销售",
    }
    assert_ok(client.put("/api/profile", json=profile_payload), "update profile")

    top_ids = [job["id"] for job in jobs_payload[: min(40, len(jobs_payload))]]


def score_job(job_id: int) -> tuple[int, float]:
    started = time.perf_counter()
    with httpx.Client(base_url=BASE_URL, timeout=20) as client:
        response = assert_ok(client.post(f"/api/jobs/{job_id}/score"), f"score {job_id}")
    return response.json()["total"], time.perf_counter() - started


score_started = time.perf_counter()
latencies: list[float] = []
with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
    futures = [pool.submit(score_job, job_id) for job_id in top_ids]
    for future in as_completed(futures):
        _score, elapsed = future.result()
        latencies.append(elapsed)
score_elapsed = time.perf_counter() - score_started
latencies.sort()
p95 = latencies[int(len(latencies) * 0.95) - 1] if latencies else 0
print(f"score {len(top_ids)} jobs with concurrency {CONCURRENCY}: {score_elapsed:.3f}s, p95 {p95:.3f}s")

with httpx.Client(base_url=BASE_URL, timeout=30) as client:
    brief, brief_elapsed = timed(
        "sprint brief",
        lambda: assert_ok(client.post("/api/sprint/brief", params={"top_n": 20, "prep_n": 5}), "sprint brief").json(),
    )
    if len(brief["top_jobs"]) != min(20, len(jobs_payload)):
        raise AssertionError("unexpected sprint top job count")

budgets = {
    "import_elapsed": import_elapsed,
    "list_elapsed": list_elapsed,
    "score_elapsed": score_elapsed,
    "score_p95": p95,
    "brief_elapsed": brief_elapsed,
}
thresholds = {
    "import_elapsed": 8.0,
    "list_elapsed": 3.0,
    "score_elapsed": 15.0,
    "score_p95": 5.0,
    "brief_elapsed": 8.0,
}
failures = [f"{key}={value:.3f}s > {thresholds[key]:.3f}s" for key, value in budgets.items() if value > thresholds[key]]
if failures:
    raise SystemExit("Load smoke budget failed: " + "; ".join(failures))

print("Load smoke passed")
PY
