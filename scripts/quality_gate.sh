#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

section() {
  printf '\n== %s ==\n' "$1"
}

# 单一清理点：本脚本有两处要建临时目录（干净检出、旧库迁移烟测）。各写一个
# `trap ... EXIT` 会互相覆盖——后注册的赢，先建的那个目录就泄漏在 /tmp 里。
CLEANUP_PATHS=()
cleanup_all() {
  if [[ ${#CLEANUP_PATHS[@]} -gt 0 ]]; then
    rm -rf "${CLEANUP_PATHS[@]}"
  fi
}
trap cleanup_all EXIT

require_file() {
  if [[ ! -e "$1" ]]; then
    echo "Missing $1"
    exit 1
  fi
}

section "Prerequisites"
require_file ".venv/bin/python"
require_file "frontend/node_modules"

section "Script Syntax"
bash -n scripts/dev_wsl.sh
bash -n scripts/system_smoke.sh
bash -n scripts/load_smoke.sh
bash -n scripts/chat_stress.sh
bash -n scripts/lib/testing_env.sh
bash -n tools/host_collect_opencli.sh

section "Config Example Guard"
# 从已删除的 scripts/deploy_check.sh 搬来的两条守卫（那个脚本的其余部分是 Docker 专用）。
# 搬进门禁而不是留在一个要「记得手动跑」的脚本里：config.example.yaml 是入库的公开模板，
# 混进真实密钥就是直接泄密，而它同时也是测试基线的来源（scripts/lib/testing_config.py），
# 解析不了会让整套测试连基线都建不起来。
if grep -RInE "(api_key|apikey|authorization|password|secret|token)[[:space:]]*:" config.example.yaml; then
  echo "config.example.yaml 出现疑似敏感键名。密钥只进 .env，不写入配置模板。"
  exit 1
fi
echo "ok: config.example.yaml 无敏感键名"
.venv/bin/python - <<'PY'
from pathlib import Path

import yaml

path = Path("config.example.yaml")
try:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
except yaml.YAMLError as exc:
    raise SystemExit(f"config.example.yaml YAML 解析失败：{exc}") from exc
if not isinstance(loaded, dict):
    raise SystemExit("config.example.yaml 根节点必须是对象")
print("ok: config.example.yaml 可解析")
PY

section "Backend Tests"
.venv/bin/python -m pytest -q

section "Clean Checkout Tests"
# 在一个**没有**机主 `.env` / `config.yaml` 的检出里再跑一遍全套测试。
#
# 为什么值得多花一次 pytest 的时间：这两个文件都被 gitignore，是本机独有的，而测试曾经
# 静默依赖它们——`config.yaml` 里的 `ai.enabled: true` 撑着 23 个 ingest 用例，
# 缺了它们在干净检出（新机器、CI）里直接 25 红。这类回归在开发机上永远看不见，
# 只有真的在没有个人配置的检出里跑一遍才暴露得出来。
#
# 用 `git ls-files` 而不是 `git archive HEAD`：要连未提交改动一起验，否则本次改动本身
# 不在校验范围内。软链 .venv 复用依赖，不重装。
#
# 代价是「未 git add 的新文件不在其中」——新增的测试或支撑模块忘了 add，这一段会静默跳过
# 它（实测踩过两次：新 conftest 依赖的模块没 add，干净检出里直接 ImportError）。所以先报
# 未跟踪文件，别让"少测了"看起来像"通过了"。
untracked="$(git ls-files --others --exclude-standard)"
if [[ -n "$untracked" ]]; then
  echo "以下文件未被 git 跟踪，不会进入干净检出校验（如属本次改动请先 git add）："
  printf '  %s\n' $untracked
fi
clean_checkout_dir="$(mktemp -d)"
CLEANUP_PATHS+=("$clean_checkout_dir")
git ls-files -z | tar --null -T - -cf - | tar -xf - -C "$clean_checkout_dir"
ln -s "$ROOT_DIR/.venv" "$clean_checkout_dir/.venv"
for personal in .env config.yaml; do
  if [[ -e "$clean_checkout_dir/$personal" ]]; then
    echo "干净检出里出现了个人文件 $personal —— 它本该被 gitignore，请检查 .gitignore"
    exit 1
  fi
done
(
  cd "$clean_checkout_dir"
  .venv/bin/python -m pytest -q
)

section "Frontend Build"
(
  cd frontend
  npm run build
)

section "System Smoke"
scripts/system_smoke.sh

section "Alembic Migration Smoke"
tmp_dir="$(mktemp -d)"
CLEANUP_PATHS+=("$tmp_dir")
old_db="$tmp_dir/old-schema.sqlite3"
.venv/bin/python - "$old_db" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
conn = sqlite3.connect(db_path)
conn.execute(
    """
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY,
        source VARCHAR NOT NULL,
        external_id VARCHAR NOT NULL,
        url VARCHAR,
        title VARCHAR NOT NULL,
        company_name VARCHAR NOT NULL,
        city VARCHAR,
        area VARCHAR,
        collected_at DATETIME,
        created_at DATETIME
    )
    """
)
conn.execute(
    """
    INSERT INTO jobs (
        id, source, external_id, url, title, company_name, city, area, collected_at, created_at
    ) VALUES (
        1, 'manual', 'abc', 'https://example.com/job', 'SEO', 'Acme', 'ExampleCity', 'NorthDistrict',
        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
    )
    """
)
conn.execute(
    """
    CREATE TABLE user_profile (
        id INTEGER PRIMARY KEY,
        target_titles VARCHAR NOT NULL,
        target_cities VARCHAR NOT NULL,
        salary_min_k FLOAT NOT NULL,
        salary_max_k FLOAT NOT NULL,
        skills VARCHAR NOT NULL,
        strengths VARCHAR NOT NULL,
        dealbreakers VARCHAR NOT NULL,
        commute_preferences VARCHAR NOT NULL,
        weights JSON NOT NULL,
        updated_at DATETIME NOT NULL
    )
    """
)
conn.execute(
    """
    INSERT INTO user_profile (
        id, target_titles, target_cities, salary_min_k, salary_max_k, skills, strengths,
        dealbreakers, commute_preferences, weights, updated_at
    ) VALUES (
        1, 'SEO', 'ExampleCity', 8, 20, 'SEO,Analytics', '增长复盘',
        '单休', '示例市优先', '{}', CURRENT_TIMESTAMP
    )
    """
)
conn.execute(
    """
    CREATE TABLE interview_prep (
        id INTEGER PRIMARY KEY,
        job_id INTEGER NOT NULL,
        jd_summary TEXT NOT NULL,
        skill_gaps TEXT NOT NULL,
        resume_points TEXT NOT NULL,
        star_stories TEXT NOT NULL,
        questions_to_ask TEXT NOT NULL,
        communication_draft TEXT NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """
)
conn.execute(
    """
    INSERT INTO interview_prep (
        id, job_id, jd_summary, skill_gaps, resume_points, star_stories,
        questions_to_ask, communication_draft, created_at, updated_at
    ) VALUES (
        1, 1, 'summary', 'gaps', 'points', 'star', 'questions', 'draft',
        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
    )
    """
)
conn.commit()
conn.close()
PY
JOB_ONE_STOP_DATABASE_URL="sqlite:///$old_db" .venv/bin/python -m alembic -c backend/alembic.ini upgrade head
.venv/bin/python - "$old_db" <<'PY'
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
link_count = conn.execute("SELECT COUNT(*) FROM job_source_links").fetchone()[0]
canonical_key = conn.execute("SELECT canonical_key FROM jobs WHERE id = 1").fetchone()[0]
profile_columns = {row[1] for row in conn.execute("PRAGMA table_info(user_profile)").fetchall()}
prep_columns = {row[1] for row in conn.execute("PRAGMA table_info(interview_prep)").fetchall()}
table_names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
work_experience = conn.execute("SELECT work_experience FROM user_profile WHERE id = 1").fetchone()[0]
core_pitch, tailored_resume = conn.execute(
    "SELECT core_pitch, tailored_resume FROM interview_prep WHERE id = 1"
).fetchone()
conn.close()
if link_count != 1 or not canonical_key:
    raise SystemExit("migration smoke failed")
if "work_experience" not in profile_columns or not work_experience:
    raise SystemExit("profile migration smoke failed")
if not {"core_pitch", "tailored_resume"} <= prep_columns or core_pitch is None or tailored_resume is None:
    raise SystemExit("prep migration smoke failed")
if not {"chat_threads", "chat_messages", "analysis_runs"} <= table_names:
    raise SystemExit("decision chat migration smoke failed")
PY

section "Quality Gate Passed"
