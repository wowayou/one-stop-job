#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

section() {
  printf '\n== %s ==\n' "$1"
}

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
bash -n scripts/deploy_check.sh
bash -n scripts/system_smoke.sh
bash -n scripts/load_smoke.sh
bash -n scripts/docker_doctor.sh
bash -n tools/host_collect_opencli.sh

section "Backend Tests"
.venv/bin/python -m pytest -q

section "Frontend Build"
(
  cd frontend
  npm run build
)

section "System Smoke"
scripts/system_smoke.sh

section "Alembic Migration Smoke"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
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
