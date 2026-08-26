"""版本号单一事实源：`VERSION` 与五处清单必须逐字一致。

新增这条测试的原因：升级检查靠语义版本比较判断"有没有新版本"，一旦安装包版本
（tauri.conf.json）、后端上报版本（backend/app/version.py）和 npm/cargo 清单彼此漂移，
用户会看到"已是最新"却装着旧包，或刚装完就被提示升级。改版本号的正确姿势是编辑
`VERSION` 再跑 `python3 scripts/sync_version.py`。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

# (文件, 取出版本号的正则)：与 scripts/sync_version.py 的 TARGETS 一一对应。
CASES = [
    ("frontend/package.json", r'"version"\s*:\s*"([^"]+)"'),
    ("src-tauri/tauri.conf.json", r'"version"\s*:\s*"([^"]+)"'),
    ("src-tauri/Cargo.toml", r'(?m)^version\s*=\s*"([^"]+)"'),
    ("src-tauri/Cargo.lock", r'(?m)^name = "job-one-stop"\nversion = "([^"]+)"'),
    ("backend/app/version.py", r'(?m)^APP_VERSION = "([^"]+)"'),
]


def test_version_file_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION), f"VERSION 必须是 X.Y.Z，收到 {VERSION!r}"


@pytest.mark.parametrize(("rel", "pattern"), CASES, ids=[rel for rel, _ in CASES])
def test_manifest_version_matches_version_file(rel: str, pattern: str):
    text = (ROOT / rel).read_text(encoding="utf-8")
    match = re.search(pattern, text)
    assert match, f"{rel} 里没找到版本号字段（清单结构变了？同步脚本也要一起改）"
    assert match.group(1) == VERSION, (
        f"{rel} 是 {match.group(1)}，VERSION 是 {VERSION}；跑 python3 scripts/sync_version.py 同步"
    )


def test_backend_app_version_constant_matches_version_file():
    from backend.app.version import APP_VERSION

    assert APP_VERSION == VERSION


def test_whats_new_version_matches_version_file():
    """升级后首次启动的「本版新增」弹窗必须跟着版本号一起更新。

    `frontend/src/lib/whatsNew.ts` 的 `version` 与后端上报版本不相等时，弹窗**静默不弹**
    （宁可不弹也不显示上一版的说明，见组件注释）。这条断言把"忘记改文案"从静默失效变成
    测试翻红——刻意**不**让 `scripts/sync_version.py` 自动改它：自动改会让 0.3.0 的功能列表
    挂上 0.4.0 的版本号，那比不弹更糟。
    """
    text = (ROOT / "frontend/src/lib/whatsNew.ts").read_text(encoding="utf-8")
    match = re.search(r'(?m)^\s*version:\s*"([^"]+)"', text)
    assert match, "whatsNew.ts 里没找到 WHATS_NEW.version"
    assert match.group(1) == VERSION, (
        f"whatsNew.ts 是 {match.group(1)}，VERSION 是 {VERSION}；"
        "改版本号时要一起重写 headline/highlights/preserved 三段文案，否则升级弹窗不会出现"
    )
