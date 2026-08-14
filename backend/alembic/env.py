from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from backend.app.config import get_settings
from backend.app import models  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
if config.config_file_name is not None:
    # disable_existing_loggers 默认 True，会把**此前已创建的所有 logger** 关掉。
    # init_db() 是在 uvicorn 启动期跑迁移的，于是 uvicorn.access/uvicorn.error 和
    # backend.app.main 的 logger 从迁移那一刻起全部静默：「Telegram getUpdates 失败」
    # 「晨间定时采集失败」「日清单未送达」这些关键告警一条都不会落进 backend.log
    # （实测踩过——推送整天没到，日志里却干净得像没跑过）。迁移自己的日志不需要
    # 独占日志系统，这里显式关掉这个副作用。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
