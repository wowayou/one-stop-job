"""PyInstaller entry point.

This file is used by job-one-stop-backend.spec as the entry point.
It must NOT use relative imports (from . import ...) because PyInstaller
runs it as a top-level script, not as part of a package.

Instead, it uses absolute imports and calls uvicorn programmatically.
"""
import uvicorn
from backend.app.config import get_settings

settings = get_settings()
port = int(getattr(settings, "port", 8000))

if __name__ == "__main__":
    uvicorn.run(
        "backend.app.main:app",
        host="127.0.0.1",
        port=port,
        log_level="info",
    )
