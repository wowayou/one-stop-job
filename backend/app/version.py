"""应用版本号的后端读取点。

**唯一事实源是仓库根目录的 `VERSION` 文件**；本模块的 `APP_VERSION` 是它的镜像，
由 `scripts/sync_version.py` 一起刷新（同时刷新 frontend/package.json、
src-tauri/Cargo.toml、src-tauri/Cargo.lock、src-tauri/tauri.conf.json）。
`tests/test_version_sync.py` 会断言五处与 `VERSION` 完全一致，漂移即测试翻红。

打包成 PyInstaller 单文件后仓库不在旁边，所以这里**不读文件**，只留常量——
版本号必须在构建期就固化进二进制。
"""

from __future__ import annotations

APP_VERSION = "0.3.0"
