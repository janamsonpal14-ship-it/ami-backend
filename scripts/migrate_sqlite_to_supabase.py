"""Migrate all data from local SQLite database (amico.db) to Supabase PostgreSQL."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import psycopg2
from psycopg2.extras import execute_batch
from sqlalchemy import Boolean
from app.database import Base
import app.models

SQLITE_DB = BASE_DIR / "data" / "amico.db"
SUPABASE_URL = os.environ.get(
    "MIGRATION_DATABASE_URL",
    "postgresql://postgres.cmpajseqaxqlqsciwjun:htj6zXEl8dQbUE6T@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres?sslmode=require"
)

TABLES_ORDER = [
    # Level 0 (no foreign keys)
    "users",
    "clients",
    "advocates",
    "court_holidays",
    "letterhead_templates",
    "office_settings",
    "bare_acts",
    "research_configs",
    "ai_usage_ledger",
    "audit_events",
    "import_batches",
    "ecourts_case_cache",
    "ecourts_order_cache",
    "ecourts_query_cache",

    # Level 1 (depends on Level 0)
    "matters",
    "bare_act_sections",
    "bare_act_versions",
    "fee_memos",

    # Level 2 (depends on matters / acts / sections / memos)
    "client_matters",
    "matter_advocates",
    "hearings",
    "calendar_events",
    "tasks",
    "fee_memo_lines",
    "fee_memo_matters",
    "fee_adjustments",
    "payments",
    "legislative_amendments",
    "bare_act_ai_analysis",
    "research_query_logs",

    # Level 3 (depends on hearings / calendar_events)
    "hearing_advocates",
    "hearing_revisions",
    "event_advocates",
]


def migrate():
    if not SQLITE_DB.exists():
        print(f"Error: SQLite database {SQLITE_DB} does not exist.", flush=True)
        sys.exit(1)

    print(f"Connecting to SQLite: {SQLITE_DB}", flush=True)
    s_conn = sqlite3.connect(str(SQLITE_DB))
    s_conn.row_factory = sqlite3.Row
    s_cur = s_conn.cursor()

    print(f"Connecting to Supabase PostgreSQL at {SUPABASE_URL.split('@')[-1]}...", flush=True)
    pg_conn = psycopg2.connect(SUPABASE_URL)
    pg_cur = pg_conn.cursor()

    total_migrated = 0

    for table in TABLES_ORDER:
        if table not in Base.metadata.tables:
            continue

        table_obj = Base.metadata.tables[table]
        bool_cols = {c.name for c in table_obj.columns if isinstance(c.type, Boolean)}
        target_cols = {c.name for c in table_obj.columns}

        # Check if table exists in SQLite
        s_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (table,))
        if not s_cur.fetchone():
            continue

        s_cur.execute(f'SELECT * FROM "{table}";')
        rows = s_cur.fetchall()
        if not rows:
            print(f"  - {table}: 0 rows (skipped)", flush=True)
            continue

        cols = [desc[0] for desc in s_cur.description]
        valid_cols = [c for c in cols if c in target_cols]

        col_list_str = ", ".join(f'"{c}"' for c in valid_cols)
        placeholders = ", ".join("%s" for _ in valid_cols)

        # Build conflict clause: if table has PK, use it or plain DO NOTHING
        pks = [c.name for c in table_obj.primary_key.columns]
        if pks:
            pk_str = ", ".join(f'"{p}"' for p in pks)
            conflict_clause = f"ON CONFLICT ({pk_str}) DO NOTHING"
        else:
            conflict_clause = "ON CONFLICT DO NOTHING"

        insert_sql = f'INSERT INTO "{table}" ({col_list_str}) VALUES ({placeholders}) {conflict_clause};'

        batch_values = []
        for r in rows:
            vals = []
            for c in valid_cols:
                v = r[c]
                if c in bool_cols and v is not None:
                    v = bool(v)
                vals.append(v)
            batch_values.append(vals)

        # Batch execute with psycopg2
        execute_batch(pg_cur, insert_sql, batch_values, page_size=100)
        pg_conn.commit()
        print(f"  ✓ {table}: migrated {len(batch_values)} rows", flush=True)
        total_migrated += len(batch_values)

        # Update sequence if table has integer primary key 'id'
        if "id" in target_cols:
            try:
                pg_cur.execute(f"""
                    SELECT setval(
                        pg_get_serial_sequence('"{table}"', 'id'),
                        COALESCE((SELECT MAX(id) FROM "{table}"), 1)
                    );
                """)
                pg_conn.commit()
            except Exception:
                pg_conn.rollback()

    pg_cur.close()
    pg_conn.close()
    s_conn.close()

    print(f"\n✓ Supabase migration completed! Total rows processed: {total_migrated}", flush=True)


if __name__ == "__main__":
    migrate()
