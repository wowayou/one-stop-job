#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT_DIR/.venv/bin/python"
HOST="127.0.0.1"

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

db_path="$tmp_dir/system-smoke.sqlite3"
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
health_url = base_url + "/api/health"

for _ in range(60):
    try:
        with urllib.request.urlopen(health_url, timeout=1) as response:
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

BASE_URL="$base_url" "$PYTHON" - <<'PY'
from __future__ import annotations

import os

import httpx


BASE_URL = os.environ["BASE_URL"]


def assert_ok(response: httpx.Response, label: str) -> httpx.Response:
    if response.status_code >= 400:
        raise AssertionError(f"{label} failed: {response.status_code} {response.text}")
    return response


def sources(job: dict) -> set[str]:
    return {link["source"] for link in job.get("source_links") or []}


with httpx.Client(base_url=BASE_URL, timeout=10) as client:
    health = assert_ok(client.get("/api/health"), "health").json()
    assert health["status"] == "ok"

    ai_status = assert_ok(client.get("/api/ai/status"), "ai status").json()
    assert "api_key_configured" in ai_status
    assert "base_url_configured" in ai_status

    app_config = assert_ok(client.get("/api/config"), "app config").json()
    assert "config" in app_config
    assert "env" in app_config
    secret_rejected = client.put("/api/config", json={"config": {"ai": {"api_key": "sk-system-smoke"}}})
    assert secret_rejected.status_code == 400
    app_config["config"]["ai"] = {"enabled": True, "provider": "openai_compatible"}
    saved_config = assert_ok(client.put("/api/config", json={"config": app_config["config"]}), "save app config").json()
    assert saved_config["config"]["ai"]["enabled"] is True
    ai_status = assert_ok(client.get("/api/ai/status"), "ai status after config save").json()
    assert ai_status["enabled_in_config"] is True

    sources_payload = assert_ok(client.get("/api/sources"), "list sources").json()
    source_keys = {item["key"] for item in sources_payload}
    assert {"boss", "bebee", "zhilian"} <= source_keys
    assert next(item for item in sources_payload if item["key"] == "zhilian")["enabled"] is False

    first = assert_ok(
        client.post(
            "/api/jobs",
            json={
                "title": "SEO运营",
                "company_name": "示例市系统测试科技有限公司",
                "salary_text": "8-12K",
                "city": "示例市",
                "area": "中心区",
                "skills": "SEO,数据分析,内容运营",
                "description": "负责官网SEO和内容增长",
            },
        ),
        "create first job",
    ).json()
    assert sources(first) == {"manual"}

    patched = assert_ok(
        client.patch(f"/api/jobs/{first['id']}", json={"status": "researching", "favorite": True}),
        "patch first job",
    ).json()
    assert patched["status"] == "researching"
    assert patched["favorite"] is True

    reverted = assert_ok(
        client.patch(f"/api/jobs/{first['id']}", json={"status": "new"}),
        "revert first job status",
    ).json()
    assert reverted["status"] == "new"

    bulk_empty = assert_ok(
        client.patch("/api/jobs/bulk", json={"ids": [], "status": "researching"}),
        "empty bulk job update",
    ).json()
    assert bulk_empty == {"updated": 0, "jobs": []}

    csv_payload = "title,company,salary,area,skills\nSEO运营,示例市系统测试科技有限公司,9-13K,示例市·中心区,SEO/数据分析\n".encode()
    imported = assert_ok(
        client.post(
            "/api/jobs/import",
            params={"source": "导入文件"},
            files={"file": ("jobs.csv", csv_payload, "text/csv")},
        ),
        "csv import",
    ).json()
    assert imported == {"fetched": 1, "created": 0, "updated": 1}

    imported_jobs = assert_ok(client.get("/api/jobs", params={"source": "导入文件"}), "source filter").json()
    assert len(imported_jobs) == 1
    assert sources(imported_jobs[0]) == {"manual", "导入文件"}
    first = imported_jobs[0]

    company_id = first["company_id"]
    assert company_id
    updated_company = assert_ok(
        client.patch(
            f"/api/companies/{company_id}",
            json={
                "website": "https://example.com",
                "industry": "软件服务",
                "risk_level": "low",
                "notes": "系统测试样例",
            },
        ),
        "update company",
    ).json()
    assert updated_company["risk_level"] == "low"

    research = assert_ok(
        client.post(
            f"/api/companies/{company_id}/research",
            json={
                "source_type": "manual_note",
                "title": "官网和招聘页核对",
                "summary": "岗位描述与公司业务一致，暂无明显负面。",
                "sentiment": "positive",
                "confidence": 0.8,
            },
        ),
        "add research",
    ).json()
    assert research["sentiment"] == "positive"

    company_detail = assert_ok(client.get(f"/api/companies/{company_id}"), "company detail").json()
    assert company_detail["research_items"]
    assert company_detail["jobs"]

    profile = assert_ok(
        client.put(
            "/api/profile",
            json={
                "target_titles": "SEO,运营",
                "target_cities": "示例市,中心区",
                "salary_min_k": 7,
                "salary_max_k": 15,
                "skills": "SEO,数据分析,内容运营",
                "strengths": "能把关键词机会、内容优化和数据复盘串成闭环",
                "work_experience": "负责独立站 SEO 项目，完成关键词分层、内容规划和落地页优化，带来询盘增长。",
                "dealbreakers": "单休,纯销售",
            },
        ),
        "update profile",
    ).json()
    assert profile["salary_min_k"] == 7

    chat_thread = assert_ok(
        client.post("/api/chat/threads", json={"kind": "job", "job_id": first["id"]}),
        "create job chat",
    ).json()
    chat_reply = assert_ok(
        client.post(
            f"/api/chat/threads/{chat_thread['id']}/messages",
            json={"content": "这个岗位值得继续推进吗？"},
        ),
        "create chat message",
    ).json()
    assert chat_reply["ai_used"] is False
    assert chat_reply["analysis"]["priority"] in {"A", "B", "C", "D", "待确认"}
    chat_detail = assert_ok(client.get(f"/api/chat/threads/{chat_thread['id']}"), "restore chat thread").json()
    assert [message["role"] for message in chat_detail["messages"]] == ["user", "assistant"]

    score = assert_ok(client.post(f"/api/jobs/{first['id']}/score"), "create score").json()
    assert score["total"] > 0

    prep = assert_ok(client.post(f"/api/jobs/{first['id']}/prep"), "create prep").json()
    assert prep["communication_draft"]
    assert "独立站 SEO 项目" in prep["core_pitch"]
    assert "定制简历" in prep["tailored_resume"]

    drafts = assert_ok(client.get("/api/drafts"), "list drafts").json()
    assert any(draft["job_id"] == first["id"] for draft in drafts)
    draft_kinds = {draft["kind"] for draft in drafts if draft["job_id"] == first["id"]}
    assert {"communication_draft", "core_pitch", "tailored_resume"} <= draft_kinds

    task = assert_ok(
        client.post(
            "/api/follow-ups",
            json={"job_id": first["id"], "title": "联系招聘人", "status": "todo", "due_date": "2026-06-09"},
        ),
        "create task",
    ).json()
    task = assert_ok(
        client.patch(f"/api/follow-ups/{task['id']}", json={"status": "done", "title": "联系招聘人并记录反馈"}),
        "update task",
    ).json()
    assert task["status"] == "done"
    deleted = assert_ok(client.delete(f"/api/follow-ups/{task['id']}"), "delete task").json()
    assert deleted["deleted"] is True

    second = assert_ok(
        client.post(
            "/api/jobs",
            json={
                "title": "外贸SEO运营",
                "company_name": "示例市品牌出海科技有限公司",
                "salary_text": "10-15K",
                "city": "示例市",
                "area": "北区",
                "skills": "SEO,外贸运营,Google Analytics",
            },
        ),
        "create second job",
    ).json()

    bulk_update = assert_ok(
        client.patch(
            "/api/jobs/bulk",
            json={"ids": [first["id"], second["id"], 99999], "status": "researching", "favorite": True},
        ),
        "bulk job update",
    ).json()
    assert bulk_update["updated"] == 2
    assert {job["id"] for job in bulk_update["jobs"]} == {first["id"], second["id"]}
    assert all(job["status"] == "researching" and job["favorite"] is True for job in bulk_update["jobs"])

    brief = assert_ok(
        client.post("/api/sprint/brief", params={"top_n": 10, "prep_n": 2, "create_tasks": "true"}),
        "sprint brief",
    ).json()
    top_ids = {job["id"] for job in brief["top_jobs"]}
    assert {first["id"], second["id"]} <= top_ids
    assert "今日求职冲刺包" in brief["markdown"]
    assert brief["tasks_created"]

    repeated = assert_ok(
        client.post("/api/sprint/brief", params={"top_n": 10, "prep_n": 2, "create_tasks": "true"}),
        "repeat sprint brief",
    ).json()
    assert repeated["tasks_created"] == []

    wechat_run = assert_ok(
        client.post(
            "/api/collect/wechat",
            json={
                "bodies": {
                    "https://mp.weixin.qq.com/s/SystemSmoke": (
                        "示例市系统测试科技有限公司招聘\n"
                        "【SEO运营】\n"
                        "公司：示例市系统测试科技有限公司\n"
                        "薪资：8-12K\n"
                        "工作地点：示例市·中心区\n"
                        "岗位职责：负责SEO优化和内容增长\n"
                        "任职要求：熟悉SEO和数据分析"
                    )
                }
            },
        ),
        "wechat body collect",
    ).json()
    assert wechat_run["status"] == "success"
    assert wechat_run["fetched_count"] >= 1

    wechat_jobs = assert_ok(client.get("/api/jobs", params={"source": "公众号"}), "wechat source filter").json()
    assert wechat_jobs
    assert "公众号" in sources(wechat_jobs[0])

    runs = assert_ok(client.get("/api/collect/runs"), "collect runs").json()
    assert runs

print("System smoke passed")
PY

echo "System smoke passed at $base_url"
