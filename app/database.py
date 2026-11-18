from __future__ import annotations

import os
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
            try:
                cursor.execute("PRAGMA journal_mode = WAL;")
                cursor.execute("PRAGMA synchronous = NORMAL;")
            except Exception:
                pass
        cursor.close()

    return engine


def create_app_engine():
    """Create database engine: PostgreSQL/Supabase when DATABASE_URL is set, otherwise SQLite."""
    raw_url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("SUPABASE_DATABASE_URL")
        or os.environ.get("AMI_DATABASE_URL")
    )
    if raw_url:
        url = raw_url.strip()
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg2://", 1)
        elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

        return create_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=300,
            future=True,
        )
    return create_sqlite_engine()


engine = create_app_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """Yield a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_additive_schema(target_engine=None) -> None:
    """Safely add additive columns and ensure all tables exist."""
    eng = target_engine or engine
    if eng.dialect.name == "sqlite":
        with eng.begin() as conn:
            cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(bare_acts);").fetchall()}
            if "file_path" not in cols:
                conn.exec_driver_sql("ALTER TABLE bare_acts ADD COLUMN file_path VARCHAR(500);")
            if "page_count" not in cols:
                conn.exec_driver_sql("ALTER TABLE bare_acts ADD COLUMN page_count INTEGER NOT NULL DEFAULT 0;")
            if "section_count" not in cols:
                conn.exec_driver_sql("ALTER TABLE bare_acts ADD COLUMN section_count INTEGER NOT NULL DEFAULT 0;")
            if "act_no" not in cols:
                conn.exec_driver_sql("ALTER TABLE bare_acts ADD COLUMN act_no VARCHAR(40);")
            if "category_slug" not in cols:
                conn.exec_driver_sql("ALTER TABLE bare_acts ADD COLUMN category_slug VARCHAR(100);")
            if "enactment_date" not in cols:
                conn.exec_driver_sql("ALTER TABLE bare_acts ADD COLUMN enactment_date DATE;")
            if "commencement_date" not in cols:
                conn.exec_driver_sql("ALTER TABLE bare_acts ADD COLUMN commencement_date DATE;")
            if "source_url" not in cols:
                conn.exec_driver_sql("ALTER TABLE bare_acts ADD COLUMN source_url VARCHAR(500);")
            if "sha256_hash" not in cols:
                conn.exec_driver_sql("ALTER TABLE bare_acts ADD COLUMN sha256_hash VARCHAR(64);")

            # Additive column for Part 4 judgments cache
            tables = {row[0] for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
            if "bare_act_ai_analysis" in tables:
                analysis_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(bare_act_ai_analysis);").fetchall()}
                if "judgments_json" not in analysis_cols:
                    conn.exec_driver_sql("ALTER TABLE bare_act_ai_analysis ADD COLUMN judgments_json TEXT NOT NULL DEFAULT '[]';")

            # Additive columns for eCourts integration on matters
            if "matters" in tables:
                matter_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(matters);").fetchall()}
                if "cnr" not in matter_cols:
                    conn.exec_driver_sql("ALTER TABLE matters ADD COLUMN cnr VARCHAR(30);")
                if "ecourts_synced_at" not in matter_cols:
                    conn.exec_driver_sql("ALTER TABLE matters ADD COLUMN ecourts_synced_at DATETIME;")
                if "ecourts_data_json" not in matter_cols:
                    conn.exec_driver_sql("ALTER TABLE matters ADD COLUMN ecourts_data_json TEXT;")

            # Create eCourts cache tables if not present
            Base.metadata.create_all(conn)
    else:
        # PostgreSQL / Supabase
        with eng.begin() as conn:
            Base.metadata.create_all(conn)


