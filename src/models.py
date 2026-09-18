"""
Pocketful Ledger Database Models & Connection Helpers.

Enforces SQLite WAL mode, foreign keys, non-negative balance constraints,
and schema definitions for double-entry bookkeeping.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_DB_PATH = str(DEFAULT_DB_DIR / "pocketful.db")
SYSTEM_CLEARING_ACCOUNT_ID = "SYSTEM_CLEARING"


def utc_now_iso() -> str:
    """Returns current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: Optional[str] = None, timeout: float = 30.0) -> sqlite3.Connection:
    """
    Creates and configures a SQLite connection with WAL mode and appropriate pragmas.
    
    Uses isolation_level=None to operate in autocommit mode, allowing explicit
    transaction control with BEGIN IMMEDIATE to serialize writes and prevent deadlocks.
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH

    # If it's a file path (not :memory:), make sure parent directories exist
    if db_path != ":memory:" and not db_path.startswith("file:"):
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    conn = sqlite3.connect(
        db_path,
        timeout=timeout,
        isolation_level=None,  # Explicit transaction management
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row

    # Performance & Concurrency Pragmas
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)};")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA cache_size = -64000;")  # 64 MB cache
    conn.execute("PRAGMA temp_store = MEMORY;")

    return conn


@contextmanager
def get_db(db_path: Optional[str] = None, timeout: float = 30.0) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for obtaining a database connection."""
    conn = get_connection(db_path=db_path, timeout=timeout)
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Optional[str] = None) -> None:
    """
    Initializes database schema and default system clearing account.
    """
    with get_db(db_path=db_path) as conn:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            # 1. Accounts Table: CHECK (balance_cents >= 0 OR type = 'SYSTEM')
            # Ensures user accounts can never go negative at the database engine level.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL DEFAULT 'USER' CHECK (type IN ('USER', 'SYSTEM')),
                    balance_cents INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    CHECK (balance_cents >= 0 OR type = 'SYSTEM')
                );
            """)

            # 2. Journal Entries Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS journal_entries (
                    id TEXT PRIMARY KEY,
                    entry_type TEXT NOT NULL CHECK (entry_type IN ('DEPOSIT', 'TRANSFER', 'WITHDRAWAL', 'AUDIT_ADJUSTMENT')),
                    description TEXT,
                    created_at TEXT NOT NULL
                );
            """)

            # 3. Ledger Lines Table
            # Every financial transaction generates >= 2 lines summing to exactly 0.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ledger_lines (
                    id TEXT PRIMARY KEY,
                    journal_entry_id TEXT NOT NULL REFERENCES journal_entries(id) ON DELETE RESTRICT,
                    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
                    amount_cents INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

            # 4. Idempotency Keys Table
            # Manages atomic states: IN_FLIGHT, COMPLETED, FAILED
            conn.execute("""
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    key TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('IN_FLIGHT', 'COMPLETED', 'FAILED')),
                    journal_entry_id TEXT REFERENCES journal_entries(id) ON DELETE SET NULL,
                    response_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            # Indexes for optimal query performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_lines_journal ON ledger_lines(journal_entry_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_lines_account ON ledger_lines(account_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_type ON accounts(type);")

            # Seed system clearing account if not present
            conn.execute(
                """
                INSERT OR IGNORE INTO accounts (id, name, type, balance_cents, created_at)
                VALUES (?, ?, 'SYSTEM', 0, ?);
                """,
                (SYSTEM_CLEARING_ACCOUNT_ID, "System Clearing Reserve", utc_now_iso())
            )

            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise
