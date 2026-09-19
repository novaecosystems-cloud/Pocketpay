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
            # Includes pending_debit_cents and pending_credit_cents for TigerBeetle-style two-phase holds.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL DEFAULT 'USER' CHECK (type IN ('USER', 'SYSTEM')),
                    balance_cents INTEGER NOT NULL DEFAULT 0,
                    pending_debit_cents INTEGER NOT NULL DEFAULT 0,
                    pending_credit_cents INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    CHECK (balance_cents >= 0 OR type = 'SYSTEM'),
                    CHECK (pending_debit_cents >= 0),
                    CHECK (pending_credit_cents >= 0)
                );
            """)

            # Migrate existing accounts table if missing pending columns
            existing_cols = [col[1] for col in conn.execute("PRAGMA table_info(accounts);").fetchall()]
            if "pending_debit_cents" not in existing_cols:
                conn.execute("ALTER TABLE accounts ADD COLUMN pending_debit_cents INTEGER NOT NULL DEFAULT 0;")
            if "pending_credit_cents" not in existing_cols:
                conn.execute("ALTER TABLE accounts ADD COLUMN pending_credit_cents INTEGER NOT NULL DEFAULT 0;")

            # 2. Journal Entries Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS journal_entries (
                    id TEXT PRIMARY KEY,
                    entry_type TEXT NOT NULL CHECK (entry_type IN ('DEPOSIT', 'TRANSFER', 'WITHDRAWAL', 'AUDIT_ADJUSTMENT', 'BATCH_TRANSFER')),
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

            # 5. TigerBeetle Two-Phase Pending Transfers Table
            # Manages two-phase transfers: PENDING (hold) -> POSTED (settled) or VOIDED (canceled)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_transfers (
                    id TEXT PRIMARY KEY,
                    from_account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
                    to_account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
                    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
                    status TEXT NOT NULL CHECK (status IN ('PENDING', 'POSTED', 'VOIDED')),
                    description TEXT,
                    timeout_seconds INTEGER,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    completed_at TEXT,
                    journal_entry_id TEXT REFERENCES journal_entries(id) ON DELETE SET NULL
                );
            """)

            # Indexes for optimal query performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_lines_journal ON ledger_lines(journal_entry_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_lines_account ON ledger_lines(account_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_type ON accounts(type);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_transfers_from ON pending_transfers(from_account_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_transfers_to ON pending_transfers(to_account_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pending_transfers_status ON pending_transfers(status);")

            # Seed system clearing account if not present
            conn.execute(
                """
                INSERT OR IGNORE INTO accounts (id, name, type, balance_cents, pending_debit_cents, pending_credit_cents, created_at)
                VALUES (?, ?, 'SYSTEM', 0, 0, 0, ?);
                """,
                (SYSTEM_CLEARING_ACCOUNT_ID, "System Clearing Reserve", utc_now_iso())
            )

            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise
