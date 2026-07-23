"""按域拆分的 API 路由模块（Phase R · R2）。

每个模块导出一个 `APIRouter`，由 `main.py` 统一 `include_router`。路由只依赖
`deps` / `models` / `schemas` / `services` / `config`，**绝不 import main**（避免循环依赖）。
"""
