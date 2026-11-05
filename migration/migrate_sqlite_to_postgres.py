"""Back up and idempotently migrate the legacy SQLite transactions table."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import FinancialPanditDB


DEFAULT_SQLITE_PATH = PROJECT_ROOT / "financial_pandit.db"

TRANSACTION_COLUMNS = (
    "transaction_id", "external_transaction_id", "transaction_date", "posted_date",
    "description", "merchant", "amount", "type", "category", "confidence",
    "needs_review", "created_at",
)


def backup_sqlite(source: Path, destination: Path | None = None) -> Path:
    if not source.is_file():
        raise FileNotFoundError(f"SQLite source database not found: {source}")
    destination = destination or source.with_name(
        f"{source.name}.sqlite-backup-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    )
    if destination.resolve() == source.resolve():
        raise ValueError("SQLite backup destination must differ from the source.")
    shutil.copy2(source, destination)
    return destination


def sqlite_snapshot(source: Path) -> tuple[int, Decimal, Decimal]:
    with sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True) as connection:
        count, total, expense_total = connection.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(amount), 0),
                   COALESCE(SUM(CASE WHEN type = 'EXPENSE' THEN amount ELSE 0 END), 0)
            FROM transactions
            """
        ).fetchone()
    cents = Decimal("0.01")
    return int(count), Decimal(str(total)).quantize(cents), Decimal(str(expense_total)).quantize(cents)


def migrate(source: Path, database_url: str, backup_path: Path | None = None) -> dict[str, object]:
    backup = backup_sqlite(source, backup_path)
    expected_count, expected_total, expected_expense_total = sqlite_snapshot(source)
    database = FinancialPanditDB(database_url)
    database.initialize()

    with sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True) as sqlite_connection:
        sqlite_connection.row_factory = sqlite3.Row
        rows = sqlite_connection.execute(
            "SELECT " + ", ".join(TRANSACTION_COLUMNS) + " FROM transactions ORDER BY transaction_id"
        ).fetchall()

    with psycopg.connect(database_url, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            values = []
            for row in rows:
                row_values = [row[column] for column in TRANSACTION_COLUMNS]
                # SQLite represents BOOLEAN values as 0/1 integers.
                row_values[TRANSACTION_COLUMNS.index("needs_review")] = bool(row_values[10])
                values.append(tuple(row_values))
            cursor.executemany(
                """
                INSERT INTO transactions (
                    transaction_id, external_transaction_id, transaction_date, posted_date,
                    description, merchant, amount, type, category, confidence,
                    needs_review, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (transaction_id) DO NOTHING
                """,
                values,
            )
            cursor.execute(
                """
                SELECT setval(
                    pg_get_serial_sequence('transactions', 'transaction_id'),
                    COALESCE(MAX(transaction_id), 1), MAX(transaction_id) IS NOT NULL
                )
                FROM transactions
                """
            )
            cursor.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(amount), 0),
                       COALESCE(SUM(amount) FILTER (WHERE type = 'EXPENSE'), 0)
                FROM transactions
                """
            )
            actual_count, actual_total, actual_expense_total = cursor.fetchone()

    actual_total = Decimal(str(actual_total)).quantize(Decimal("0.01"))
    actual_expense_total = Decimal(str(actual_expense_total)).quantize(Decimal("0.01"))
    verification = {
        "row_count_match": int(actual_count) == expected_count,
        "total_match": actual_total == expected_total,
        "expense_total_match": actual_expense_total == expected_expense_total,
    }
    if not all(verification.values()):
        raise RuntimeError(f"Migration verification failed: {verification}")
    return {
        "backup": str(backup), "source_rows": expected_count, "imported_rows_seen": len(rows),
        "postgres_rows": int(actual_count), "total": str(actual_total),
        "expense_total": str(actual_expense_total), "verification": verification,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up and idempotently migrate SQLite to PostgreSQL.")
    parser.add_argument("--sqlite", type=Path, default=DEFAULT_SQLITE_PATH)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("DATABASE_URL is required (the value is never printed).")
    result = migrate(args.sqlite.resolve(), args.database_url, args.backup.resolve() if args.backup else None)
    print(result)


if __name__ == "__main__":
    main()
