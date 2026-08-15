from __future__ import annotations

import sqlite3
import time
from collections.abc import AsyncGenerator
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine, select

from .config import get_settings
from .models import UserProfile


settings = get_settings()
connect_args = {"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
ALEMBIC_SCRIPT_LOCATION = Path(__file__).resolve().parents[1] / "alembic"


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def _is_sqlite_locked(exc: OperationalError) -> bool:
    original = getattr(exc, "orig", None)
    return isinstance(original, sqlite3.OperationalError) and "database is locked" in str(original).lower()


def _run_migrations() -> None:
    if not ALEMBIC_INI.exists() or not ALEMBIC_SCRIPT_LOCATION.exists():
        return
    config = AlembicConfig(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_SCRIPT_LOCATION))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def init_db() -> None:
    last_exc: OperationalError | None = None
    for attempt in range(6):
        try:
            _run_migrations()
            SQLModel.metadata.create_all(engine)
            with Session(engine) as session:
                profile = session.exec(select(UserProfile)).first()
                if profile is None:
                    session.add(UserProfile(weights=settings.scoring_weights))
                    session.commit()
                # 回收站自动清理：启动时清理超过 30 天的软删除记录
                from .services.job_ops import auto_purge_trash, cleanup_source_runs
                auto_purge_trash(session)
                cleanup_source_runs(session)
            return
        except OperationalError as exc:
            if not _is_sqlite_locked(exc):
                raise
            last_exc = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(
        "SQLite 数据库仍被占用。请先运行 stop_app.bat，或关闭其它正在使用同一 data/job_one_stop.sqlite3 的后端进程。"
    ) from last_exc


async def get_session() -> AsyncGenerator[Session, None]:
    with Session(engine) as session:
        yield session
