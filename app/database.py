from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DB_PATH, ensure_directories


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for AMI models."""


def create_sqlite_engine(db_path: Path | str | None = None):
    """Create a SQLite engine configured with foreign keys and WAL mode."""
    target_path = Path(db_path) if db_path is not None else DB_PATH
    if str(target_path) != ":memory:":
        ensure_directories()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{target_path}"
    else:
        url = "sqlite:///:memory:"

    engine = create_engine(
        url,
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        if str(target_path) != ":memory:":
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.execute("PRAGMA synchronous = NORMAL;")
        cursor.close()

    return engine


engine = create_sqlite_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """Yield a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_additive_schema(target_engine=None) -> None:
    """Safely add additive Phase 4 columns on existing pre-Phase-4 SQLite files."""
    eng = target_engine or engine
    with eng.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(bare_acts);").fetchall()}
        if "file_path" not in cols:
            conn.exec_driver_sql("ALTER TABLE bare_acts ADD COLUMN file_path VARCHAR(500);")
        if "page_count" not in cols:
            conn.exec_driver_sql("ALTER TABLE bare_acts ADD COLUMN page_count INTEGER NOT NULL DEFAULT 0;")
        if "section_count" not in cols:
            conn.exec_driver_sql("ALTER TABLE bare_acts ADD COLUMN section_count INTEGER NOT NULL DEFAULT 0;")

        # Additive column for Part 4 judgments cache
        tables = {row[0] for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
        if "bare_act_ai_analysis" in tables:
            analysis_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(bare_act_ai_analysis);").fetchall()}
            if "judgments_json" not in analysis_cols:
                conn.exec_driver_sql("ALTER TABLE bare_act_ai_analysis ADD COLUMN judgments_json TEXT NOT NULL DEFAULT '[]';")

