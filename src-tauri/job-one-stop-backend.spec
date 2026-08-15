# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: 把 FastAPI 后端打包成单文件可执行二进制，供 Tauri sidecar 使用。

用法：
    cd src-tauri
    pyinstaller job-one-stop-backend.spec --noconfirm

产物：dist/job-one-stop-backend (Linux/Mac) 或 dist/job-one-stop-backend.exe (Windows)
"""

import os
import sys
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent

block_cipher = None

a = Analysis(
    [str(ROOT / "backend" / "app" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # 前端 dist 挂载在后端 static 路径
        (str(ROOT / "frontend" / "dist"), "frontend/dist"),
        # Alembic 迁移脚本
        (str(ROOT / "backend" / "alembic"), "backend/alembic"),
        (str(ROOT / "backend" / "alembic.ini"), "backend/alembic.ini"),
    ],
    hiddenimports=[
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "fastapi",
        "sqlmodel",
        "alembic",
        "pandas",
        "openpyxl",
        "bs4",
        "lxml",
        "httpx",
        "yaml",
        "dotenv",
        "openai",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "pytest",
        "IPython",
        "notebook",
        "jupyter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="job-one-stop-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # 后端进程需要 console（Tauri 会隐藏它）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
