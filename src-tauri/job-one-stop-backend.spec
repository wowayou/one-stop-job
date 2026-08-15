# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: package FastAPI backend as a standalone binary for Tauri sidecar.

Usage:
    cd src-tauri
    pyinstaller job-one-stop-backend.spec --noconfirm

Output: dist/job-one-stop-backend (Linux/Mac) or dist/job-one-stop-backend.exe (Windows)
"""

import os
from pathlib import Path

# Project root is one level up from src-tauri
ROOT = Path(os.getcwd()).parent

block_cipher = None

a = Analysis(
    [str(ROOT / "backend" / "app" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Frontend dist is mounted by backend static handler
        (str(ROOT / "frontend" / "dist"), "frontend/dist"),
        # Alembic migration scripts
        (str(ROOT / "backend" / "alembic"), "backend/alembic"),
        (str(ROOT / "backend" / "alembic.ini"), "."),
        # Config example as fallback
        (str(ROOT / "config.example.yaml"), "."),
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
        "fastapi.middleware",
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
        "multipart",
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
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
