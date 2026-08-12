#!/usr/bin/env bash
# 聊天 / ingest 面的压测：长线程退化、线索规模、并发写、边界输入、追问锚点正确性。
#
# 与 scripts/load_smoke.sh 的分工：那个压的是**岗位管线**（批量导入、并发评分、冲刺包），
# 这个压的是**聊天入口**（ingest 落盘、决策追问、候选锚点）——两条主干各有各的热路径，
# 混在一个脚本里只会让预算互相干扰。两者都用临时 SQLite，都不读真实 data/。
#
# 用途是「改完聊天/ingest 相关代码后跑一遍，确认没把热路径改回无上界」。不进 quality_gate.sh
# （耗时几十秒，且并发预算受机器负载影响），按需手动跑。
#
# 环境变量：ROUNDS（单线程追问轮数，默认 150）、CONCURRENCY（并发写线程数，默认 12）。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT_DIR/.venv/bin/python"
HOST="127.0.0.1"
ROUNDS="${ROUNDS:-150}"
CONCURRENCY="${CONCURRENCY:-12}"

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

db_path="$tmp_dir/chat-stress.sqlite3"
inprocess_db_path="$tmp_dir/chat-stress-inprocess.sqlite3"
config_path="$tmp_dir/config.yaml"
backend_log="$tmp_dir/backend.log"
base_url="http://$HOST:$PORT"

# 临时 config：关掉 AI（压测绝不联网，CLAUDE.md §4）、关掉 Telegram 轮询、把附件目录挪进临时目录。
"$PYTHON" - "$ROOT_DIR/config.yaml" "$config_path" "$tmp_dir/data" <<'PY'
import sys

import yaml

src, dst, data_dir = sys.argv[1], sys.argv[2], sys.argv[3]
config = yaml.safe_load(open(src, encoding="utf-8")) or {}
config.setdefault("ai", {})["enabled"] = False
config["ai"].pop("providers", None)
config.setdefault("telegram", {})["enabled"] = False
config.setdefault("general", {})["data_dir"] = data_dir
yaml.safe_dump(config, open(dst, "w", encoding="utf-8"), allow_unicode=True)
PY

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

echo "== HTTP 压测（$base_url）=="
BASE_URL="$base_url" ROUNDS="$ROUNDS" CONCURRENCY="$CONCURRENCY" "$PYTHON" - <<'PY'
from __future__ import annotations

import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

BASE_URL = os.environ["BASE_URL"]
ROUNDS = int(os.environ["ROUNDS"])
CONCURRENCY = int(os.environ["CONCURRENCY"])

failures: list[str] = []


def fail(message: str) -> None:
    failures.append(message)
    print(f"  !! {message}")


def assert_ok(response: httpx.Response, label: str) -> httpx.Response:
    if response.status_code >= 400:
        raise AssertionError(f"{label} failed: {response.status_code} {response.text[:400]}")
    return response


def growth(latencies: list[float], size: int) -> tuple[float, float, float]:
    """返回 (首段均值 ms, 末段均值 ms, 倍数)：看的是**退化趋势**而不是绝对值，
    因为绝对值受机器负载影响大，趋势才反映「热路径是不是又变成随数据量线性增长了」。"""
    head = statistics.mean(latencies[:size]) * 1000
    tail = statistics.mean(latencies[-size:]) * 1000
    return head, tail, (tail / head if head else 0.0)


with httpx.Client(base_url=BASE_URL, timeout=120) as client:
    assert_ok(client.get("/api/health"), "health")

    # ---- 1. 长线程退化：「手机提问」线程是永久复用的，追问越攒越多也必须保持平稳。
    print(f"[1] 单条 general 线程连发 {ROUNDS} 次追问")
    thread_id = assert_ok(
        client.post("/api/chat/threads", json={"kind": "general", "title": "手机提问"}), "create thread"
    ).json()["id"]
    latencies = []
    for index in range(ROUNDS):
        started = time.perf_counter()
        assert_ok(
            client.post(
                f"/api/chat/threads/{thread_id}/messages",
                json={"content": f"第 {index} 个问题：这个岗位值得聊吗，15-20K 示例市中心区", "use_ai": False},
            ),
            f"message {index}",
        )
        latencies.append(time.perf_counter() - started)
    head, tail, ratio = growth(latencies, 20)
    print(f"  首 20 条 {head:.1f}ms → 末 20 条 {tail:.1f}ms（×{ratio:.2f}）")
    # 这里是**粗粒度趋势**看板：150 轮只攒 300 条消息，退化还落在噪声带里（实测去掉历史窗口
    # 也只有 ×1.60）。真正抓这条退化的是下面进程内的场景 6 和
    # tests/test_ingest.py::test_reply_in_thread_reads_a_bounded_history_window。
    if ratio > 2.0:
        fail(f"长线程线性退化：{ROUNDS} 轮后单次追问慢了 {ratio:.2f} 倍（追问只应读最近一窗历史）")
    if tail > 400:
        fail(f"长线程末端单次追问 {tail:.0f}ms > 400ms")

    # ---- 2. ingest 线索里反复追问：每次都要重算锚点（thread_candidates）。
    print(f"[2] ingest 线索连发 {ROUNDS} 次追问（每次重算候选锚点）")
    created = assert_ok(
        client.post("/api/ingest", json={"text": "招聘 独立站运营 15-20K 示例市中心区 3年经验 熟悉SEO与Google Ads"}),
        "ingest",
    ).json()
    ingest_thread_id = created["thread"]["id"]
    latencies = []
    for index in range(ROUNDS):
        started = time.perf_counter()
        assert_ok(
            client.post(
                f"/api/chat/threads/{ingest_thread_id}/messages",
                json={"content": f"追问 {index}：这个岗位风险在哪", "use_ai": False},
            ),
            f"ingest message {index}",
        )
        latencies.append(time.perf_counter() - started)
    head, tail, ratio = growth(latencies, 20)
    print(f"  首 20 条 {head:.1f}ms → 末 20 条 {tail:.1f}ms（×{ratio:.2f}）")
    # 这里的上限比场景 1 宽：thread_candidates 刻意保留了「翻遍本线索所有 assistant 消息」的
    # 行为——加窗口会让「候选埋在很老的消息里」时丢掉锚点，代价比省下的毫秒大。
    if ratio > 3.0:
        fail(f"ingest 线索退化过快：×{ratio:.2f}")

    # ---- 3. 线索规模：线索多了以后落盘与列表还快不快。
    print("[3] 建 60 条 ingest 线索后的落盘与列表耗时")
    latencies = []
    for index in range(60):
        started = time.perf_counter()
        assert_ok(
            client.post("/api/ingest", json={"text": f"招聘 运营专员{index} {10 + index % 9}-{15 + index % 9}K 示例市"}),
            f"ingest {index}",
        )
        latencies.append(time.perf_counter() - started)
    head, tail, ratio = growth(latencies, 10)
    started = time.perf_counter()
    threads = assert_ok(client.get("/api/chat/threads"), "list threads").json()
    list_ms = (time.perf_counter() - started) * 1000
    print(f"  首 10 条 {head:.1f}ms → 末 10 条 {tail:.1f}ms（×{ratio:.2f}）；列表 {len(threads)} 条 {list_ms:.1f}ms")
    if ratio > 2.5:
        fail(f"ingest 落盘随线索数退化 ×{ratio:.2f}")
    if list_ms > 1500:
        fail(f"线索列表 {len(threads)} 条耗时 {list_ms:.0f}ms > 1500ms")

    # ---- 4. 并发写：单进程里 Web 请求与 Telegram 轮询共用一个 SQLite。
    print(f"[4] {CONCURRENCY} 并发 × 12 次混合读写")
    shared_thread_id = assert_ok(
        client.post("/api/chat/threads", json={"kind": "general", "title": "并发"}), "create"
    ).json()["id"]

errors: list[str] = []
concurrent_latencies: list[float] = []


def concurrent_worker(worker_index: int) -> None:
    with httpx.Client(base_url=BASE_URL, timeout=60) as worker_client:
        for step in range(12):
            started = time.perf_counter()
            try:
                if step % 3 == 0:
                    response = worker_client.post(
                        "/api/ingest", json={"text": f"并发招聘 岗位 w{worker_index}n{step} 12-18K 示例市"}
                    )
                elif step % 3 == 1:
                    response = worker_client.post(
                        f"/api/chat/threads/{shared_thread_id}/messages",
                        json={"content": f"并发追问 w{worker_index}n{step}", "use_ai": False},
                    )
                else:
                    response = worker_client.get("/api/chat/threads")
                if response.status_code >= 400:
                    errors.append(f"w{worker_index}n{step} {response.status_code} {response.text[:160]}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"w{worker_index}n{step} EXC {type(exc).__name__}: {exc}")
            concurrent_latencies.append(time.perf_counter() - started)


started = time.perf_counter()
with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
    for future in as_completed([pool.submit(concurrent_worker, i) for i in range(CONCURRENCY)]):
        future.result()
elapsed = time.perf_counter() - started
concurrent_latencies.sort()
p95 = concurrent_latencies[int(len(concurrent_latencies) * 0.95) - 1] * 1000
print(f"  {len(concurrent_latencies)} 次请求 {elapsed:.2f}s，p95 {p95:.1f}ms，错误 {len(errors)}")
for item in errors[:5]:
    print(f"     - {item}")
if errors:
    fail(f"并发下出现 {len(errors)} 次失败请求（database is locked / 5xx 都会落在这里）")
if p95 > 3000:
    fail(f"并发 p95 {p95:.0f}ms > 3000ms")

# ---- 5. 边界输入：不该 500，也不该把空请求放行成一条空线索。
print("[5] 边界与恶意输入")
with httpx.Client(base_url=BASE_URL, timeout=120) as client:
    thread_id = assert_ok(client.post("/api/chat/threads", json={"kind": "general", "title": "边界"}), "create").json()["id"]
    cases = [
        ("超长 ingest 文本(20000)", "/api/ingest", {"text": "招聘运营 " * 2500}, {200}),
        ("超限 ingest 文本(20001)", "/api/ingest", {"text": "a" * 20001}, {422}),
        ("空 ingest", "/api/ingest", {}, {422}),
        ("纯空白 ingest", "/api/ingest", {"text": "   \n\t  "}, {422}),
        ("控制字符", "/api/ingest", {"text": "招聘\x00运营\x1b[31m 15K"}, {200, 422}),
        ("emoji/RTL/零宽", "/api/ingest", {"text": "招聘‮运营​🧨 15-20K 示例市"}, {200, 422}),
        ("200 个 URL", "/api/ingest", {"text": " ".join(f"https://example.com/{i}" for i in range(200))}, {200}),
        ("坏图片 data url", "/api/ingest", {"text": "看图", "image_data_url": "data:image/gif;base64,AAAA"}, {422}),
        ("非 base64 图片", "/api/ingest", {"text": "看图", "image_data_url": "data:image/png;base64,@@@@"}, {422}),
        ("超长消息(12000)", f"/api/chat/threads/{thread_id}/messages", {"content": "问" * 12000, "use_ai": False}, {200}),
        ("超限消息(12001)", f"/api/chat/threads/{thread_id}/messages", {"content": "问" * 12001, "use_ai": False}, {422}),
        ("candidate_index=49", f"/api/chat/threads/{thread_id}/messages", {"content": "问", "candidate_index": 49, "use_ai": False}, {200}),
        ("candidate_index=50", f"/api/chat/threads/{thread_id}/messages", {"content": "问", "candidate_index": 50, "use_ai": False}, {422}),
        ("candidate_index=-1", f"/api/chat/threads/{thread_id}/messages", {"content": "问", "candidate_index": -1, "use_ai": False}, {422}),
        ("不存在的线程", "/api/chat/threads/999999/messages", {"content": "问", "use_ai": False}, {404}),
    ]
    for label, path, payload, expected in cases:
        try:
            response = client.post(path, json=payload)
        except Exception as exc:  # noqa: BLE001
            fail(f"{label}: 请求异常 {type(exc).__name__}: {exc}")
            continue
        print(f"  {'ok' if response.status_code in expected else '!!'} {label}: {response.status_code}")
        if response.status_code not in expected:
            fail(f"{label}: 期望 {sorted(expected)}，实际 {response.status_code} {response.text[:160]}")

if failures:
    print("\nHTTP 阶段发现问题：")
    for item in failures:
        print(f"  - {item}")
    raise SystemExit(1)
print("HTTP 阶段通过")
PY

echo
echo "== 进程内压测（Telegram 专属热路径 + 锚点正确性）=="
# 这一段走不了 HTTP：回执反查只在 Telegram 轮询里调用；多候选也需要直接合成
# （规则模式下没有 LLM，HTTP 路径拆不出多个候选）。
cd "$ROOT_DIR"
OPENAI_API_KEY="" OPENAI_BASE_URL="" \
JOB_ONE_STOP_CONFIG="$config_path" \
JOB_ONE_STOP_DATABASE_URL="sqlite:///$inprocess_db_path" \
PYTHONPATH="$ROOT_DIR" "$PYTHON" - <<'PY'
from __future__ import annotations

import statistics
import time

from sqlmodel import Session

from backend.app.db import engine, init_db
from backend.app.models import ChatMessage, ChatThread
from backend.app.services.advice import build_candidate_advice, format_advice_block
from backend.app.services.chat_ingest import _find_ingest_thread_by_receipt, thread_candidates
from backend.app.services.decision_reply import reply_in_thread, resolve_thread_anchor

init_db()

failures: list[str] = []


def fail(message: str) -> None:
    failures.append(message)
    print(f"  !! {message}")


def median_ms(call, repeat: int = 5) -> float:
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        call()
        samples.append(time.perf_counter() - started)
    return statistics.median(samples) * 1000


def make_candidate(index: int) -> dict:
    return {
        "title": f"独立站运营{index}",
        "company_name": f"示例公司{index}" if index % 4 else None,
        "salary_text": "15-20K",
        "salary_min_k": 15,
        "salary_max_k": 20,
        "city": "示例市",
        "area": "中心区",
        "canonical_key": f"key-{index}" if index % 3 else None,
        "description": "负责独立站运营" * 20,
        "source": "manual",
    }


with Session(engine) as session:
    # ---- 6. 追问耗时不随线程长度增长（「手机提问」线程永久复用）。
    #
    # **这是这条退化的敏感探针**，不要指望上面 HTTP 的场景 1 来抓：那边 150 轮只攒出 300 条
    # 消息，退化程度（实测把窗口去掉后 ×1.60）还落在正常噪声带里。3000 条才拉得开：
    # 有窗口 9.5ms → 31.7ms（×3.3），去掉窗口 7.7ms → 66.4ms（×8.6）。
    # 阈值走倍数而不是绝对毫秒——机器负载会把两者同比例抬高，倍数才稳定。
    # 剩下那 ×3.3 是线程本身变大的固有成本（消息计数、取最近一条、AnalysisRun 落盘），
    # 有界且可接受，不必再压。
    print("[6] reply_in_thread 随线程消息数的耗时")
    baseline = 0.0
    for size in (10, 3000):
        thread = ChatThread(kind="general", job_id=None, title="压测线程")
        session.add(thread)
        session.commit()
        session.refresh(thread)
        for index in range(size):
            session.add(ChatMessage(thread_id=thread.id, role="user", content=f"历史消息 {index} " * 30))
        session.commit()
        elapsed = median_ms(lambda: reply_in_thread(session, thread, "这个岗位值得聊吗", use_ai=False), repeat=3)
        print(f"  线程内 {size} 条消息 → 单次追问 {elapsed:.1f}ms")
        if size == 10:
            baseline = elapsed
        else:
            # 基准值给个 8ms 下限：10 条消息那次本来就只有几毫秒，机器快的时候测出 5ms，
            # 倍数会被这点噪声凭空抬高（实测同一份正确代码 ×3.3 和 ×4.9 都出现过）。
            ratio = elapsed / max(baseline, 8.0)
            print(f"  → ×{ratio:.1f}（阈值 5.5，超过说明追问又在读整条线程历史了）")
            if ratio > 5.5 or elapsed > 300:
                fail(f"追问耗时随线程长度膨胀：{size} 条时 {elapsed:.1f}ms ×{ratio:.1f}（10 条时 {baseline:.1f}ms）")

    # ---- 7. 回执反查是有上界的（TG 每条「回复了某条消息」都要跑一次）。
    #
    # 这里只是**观测数字**，不是这条退化的守门人：实测把 500 条窗口去掉后，6000 条消息也才
    # 9ms → 12ms，要到几万条才拉得开，为此在常规压测里灌几万条不划算。真正钉住它的是
    # tests/test_ingest.py::test_receipt_lookup_scans_a_bounded_window（断言老回执确实被挤出
    # 窗口，确定性、零计时）。下面的阈值只兜灾难级回归。
    print("[7] _find_ingest_thread_by_receipt 随聊天记录总量的耗时")
    total = 0
    baseline = 0.0
    for target in (2000, 6000):
        while total < target:
            thread = ChatThread(kind="ingest", job_id=None, title=f"线索{total}")
            session.add(thread)
            session.commit()
            session.refresh(thread)
            for _ in range(10):
                session.add(
                    ChatMessage(
                        thread_id=thread.id,
                        role="assistant",
                        content="回复内容" * 50,
                        metadata_json={"analysis": {"summary": "结论" * 40}},
                    )
                )
                session.add(ChatMessage(thread_id=thread.id, role="user", content="材料" * 50))
                total += 2
            session.commit()
        elapsed = median_ms(lambda: _find_ingest_thread_by_receipt(session, 999_999))
        print(f"  库内 {total} 条消息 → 查回执 {elapsed:.1f}ms")
        if target == 2000:
            baseline = elapsed
        elif elapsed > max(200.0, baseline * 3):
            fail(f"回执反查随聊天记录膨胀：{total} 条时 {elapsed:.1f}ms（2000 条时 {baseline:.1f}ms）")

    # ---- 8. 锚点正确性：多候选 + 脏数据 + 越界指名。
    print("[8] 追问锚点正确性")
    thread = ChatThread(kind="ingest", job_id=None, title="多候选")
    session.add(thread)
    session.commit()
    session.refresh(thread)
    raw = [
        make_candidate(1),
        {"title": "  ", "company_name": None},          # 纯占位 → 跳过
        "not-a-dict",                                     # 脏数据 → 跳过
        {"title": "广告优化师", "company_name": None},    # 公司未知但有标题 → 必须保留
        make_candidate(1),                                # 同 key → 去重
        make_candidate(7),
        make_candidate(8),
        make_candidate(9),
    ]
    session.add(
        ChatMessage(thread_id=thread.id, role="assistant", content="识别到候选", metadata_json={"candidates": raw})
    )
    session.commit()

    candidates = thread_candidates(session, thread.id)
    print(f"  8 条原始候选（含 1 脏 / 1 空 / 1 重复）→ 去重后 {len(candidates)} 条")
    if any(not isinstance(item, dict) for item in candidates):
        fail("脏数据（非 dict）混进了候选列表")
    if not any(item.get("title") == "广告优化师" for item in candidates):
        fail("公司未知但有标题的候选被丢掉了（真机常见形态）")

    for index in range(len(candidates)):
        anchor = resolve_thread_anchor(session, thread, index)
        marker = "①②③④⑤⑥⑦⑧⑨⑩"[index]
        if anchor["index"] != index or not anchor["label"].startswith(marker):
            fail(f"指名 index={index} 锚定错误：{anchor}")
    print(f"  ok 指名 0..{len(candidates) - 1} 全部命中对应候选")

    out_of_range = resolve_thread_anchor(session, thread, 9)
    if out_of_range["index"] != 0:
        fail(f"越界指名未回落到第一个候选：{out_of_range}")
    print(f"  ok 越界指名回落到 {out_of_range['label']!r}")

    reply = reply_in_thread(session, thread, "这个值得聊吗", use_ai=False, candidate_index=1)
    if not reply["assistant_message"].content.startswith("针对 ②"):
        fail(f"回答未回显锚点：{reply['assistant_message'].content[:40]!r}")
    else:
        print("  ok 回答开头回显了锚点")

    # ---- 9. 脏字段候选不应炸掉锚点与回答。
    print("[9] 候选字段异常（超长 / None / 错类型）")
    thread = ChatThread(kind="ingest", job_id=None, title="脏字段")
    session.add(thread)
    session.commit()
    session.refresh(thread)
    session.add(
        ChatMessage(
            thread_id=thread.id,
            role="assistant",
            content="识别到候选",
            metadata_json={
                "candidates": [
                    {"title": "标" * 5000, "company_name": "公" * 5000, "salary_min_k": "not-a-number"},
                    {"title": "岗位B", "salary_max_k": 999999, "skills": ["列表", "不是", "字符串"]},
                    {"title": "岗位C", "city": 123, "area": {"nested": "dict"}},
                ]
            },
        )
    )
    session.commit()
    for index in range(3):
        try:
            resolve_thread_anchor(session, thread, index)
        except Exception as exc:  # noqa: BLE001
            fail(f"脏字段候选 index={index} 让锚点解析抛异常：{type(exc).__name__}: {exc}")
    try:
        reply_in_thread(session, thread, "值得吗", use_ai=False, candidate_index=0)
        print("  ok 脏字段候选下锚点与回答都没崩")
    except Exception as exc:  # noqa: BLE001
        fail(f"脏字段候选让追问抛异常：{type(exc).__name__}: {exc}")

    # ---- 10. 建议正文长度：超过 4000 会被 telegram.send_message 截断。
    print("[10] 建议正文长度 vs Telegram 截断点")
    many = [make_candidate(index) for index in range(3)]
    for candidate in many:
        candidate["title"] = "跨境电商独立站运营总监（含Google/Meta投放）" * 3
        candidate["company_name"] = "某某某某某某科技有限公司深圳分公司" * 2
    build_candidate_advice(session, many, ai_enabled=False, max_items=3)
    advice_text = format_advice_block(many)
    print(f"  3 条超长候选的建议正文 {len(advice_text)} 字符")
    if len(advice_text) > 4000:
        fail(f"建议正文 {len(advice_text)} 字符 > send_message 的 4000 截断点，手机端会被截断")

if failures:
    print("\n进程内阶段发现问题：")
    for item in failures:
        print(f"  - {item}")
    raise SystemExit(1)
print("进程内阶段通过")
PY

echo
echo "Chat stress passed"
