#!/usr/bin/env python3
"""把仓库根目录 `VERSION` 里的版本号同步到所有需要字面量的清单。

版本号唯一事实源 = `VERSION`（一行 `X.Y.Z`）。其余五处都必须写死字面量：
npm / cargo / tauri 各自的清单格式不支持"从别处读"，PyInstaller 打出的后端二进制
旁边也没有仓库文件。所以做法是：编辑 `VERSION` -> 跑本脚本 -> 提交。
`tests/test_version_sync.py` 锁住一致性，漂移会让质量门禁翻红。

用法：
    python3 scripts/sync_version.py            # 按 VERSION 同步
    python3 scripts/sync_version.py 0.4.0      # 先改写 VERSION 再同步
    python3 scripts/sync_version.py --check    # 只检查，不写入（CI 用）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def read_version() -> str:
    if not VERSION_FILE.exists():
        raise SystemExit(f"缺少版本号事实源：{VERSION_FILE}")
    value = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not SEMVER_RE.match(value):
        raise SystemExit(f"VERSION 必须是 X.Y.Z 三段语义版本，收到 {value!r}")
    return value


# (相对路径, 匹配正则, 替换模板)：每条正则都只允许命中一次，命中 0 次即报错，
# 避免清单结构变化后脚本静默什么都没改。
TARGETS: list[tuple[str, str, str]] = [
    ("frontend/package.json", r'("version"\s*:\s*")[^"]+(")', r"\g<1>{version}\g<2>"),
    ("src-tauri/tauri.conf.json", r'("version"\s*:\s*")[^"]+(")', r"\g<1>{version}\g<2>"),
    ("src-tauri/Cargo.toml", r'(?m)^(version\s*=\s*")[^"]+(")', r"\g<1>{version}\g<2>"),
    (
        "src-tauri/Cargo.lock",
        r'(?m)^(name = "job-one-stop"\nversion = ")[^"]+(")',
        r"\g<1>{version}\g<2>",
    ),
    ("backend/app/version.py", r'(?m)^(APP_VERSION = ")[^"]+(")', r"\g<1>{version}\g<2>"),
]


def sync(version: str, *, check_only: bool) -> int:
    problems: list[str] = []
    for rel, pattern, template in TARGETS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        replacement = template.format(version=version)
        updated, count = re.subn(pattern, replacement, text, count=1)
        if count != 1:
            problems.append(f"{rel}: 未找到版本号字段（正则 {pattern!r} 没有命中）")
            continue
        if updated == text:
            print(f"  = {rel} 已是 {version}")
            continue
        if check_only:
            problems.append(f"{rel}: 版本号与 VERSION({version}) 不一致")
            continue
        path.write_text(updated, encoding="utf-8")
        print(f"  ✓ {rel} -> {version}")
    if problems:
        print("\n版本号同步未通过：", file=sys.stderr)
        for item in problems:
            print(f"  ✗ {item}", file=sys.stderr)
        if check_only:
            print("  修复：python3 scripts/sync_version.py", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--check"]
    check_only = "--check" in argv
    if args:
        candidate = args[0].lstrip("vV")
        if not SEMVER_RE.match(candidate):
            raise SystemExit(f"版本号必须是 X.Y.Z，收到 {args[0]!r}")
        if check_only:
            raise SystemExit("--check 不能和显式版本号一起用")
        VERSION_FILE.write_text(f"{candidate}\n", encoding="utf-8")
        print(f"VERSION -> {candidate}")
    version = read_version()
    print(f"{'检查' if check_only else '同步'}版本号 {version}：")
    return sync(version, check_only=check_only)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
