from __future__ import annotations

import sqlite3
from pathlib import Path


class FinancialPanditDB:
    """Create and initialize the Financial Pandit SQLite database."""

    def __init__(self, db_path: str | Path = "financial_pandit.db") -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        """Create the database, tables, and indexes."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_transaction_id TEXT,
                    transaction_date DATE NOT NULL,
                    posted_date DATE,
                    description TEXT NOT NULL,
                    merchant TEXT,
                    amount DECIMAL NOT NULL,
                    "type" TEXT CHECK (
                        "type" IS NULL
                        OR "type" IN ('EXPENSE', 'INCOME', 'TRANSFER', 'OTHER')
                    ),
                    category TEXT,
                    confidence REAL CHECK (
                        confidence IS NULL OR confidence BETWEEN 0 AND 1
                    ),
                    needs_review BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_transactions_transaction_date
                    ON transactions (transaction_date);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_external_id
                    ON transactions (external_transaction_id)
                    WHERE external_transaction_id IS NOT NULL;
                """
            )
            self.ensure_transactions_type_column(connection)
            self.ensure_transactions_category_column(connection)
            self.remove_legacy_schema(connection)

    def connect(self) -> sqlite3.Connection:
        """Open a database connection with foreign key enforcement enabled."""
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys = ON;")
        return connection

    @staticmethod
    def ensure_transactions_type_column(connection: sqlite3.Connection) -> None:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(transactions);")
        }

        if "type" in columns:
            return

        connection.execute(
            """
            ALTER TABLE transactions
            ADD COLUMN "type" TEXT CHECK (
                "type" IS NULL
                OR "type" IN ('EXPENSE', 'INCOME', 'TRANSFER', 'OTHER')
            );
            """
        )

    @staticmethod
    def ensure_transactions_category_column(connection: sqlite3.Connection) -> None:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(transactions);")
        }

        if "category" in columns:
            return

        connection.execute(
            """
            ALTER TABLE transactions
            ADD COLUMN category TEXT;
            """
        )

    @staticmethod
    def remove_legacy_schema(connection: sqlite3.Connection) -> None:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(transactions);")
        }
        legacy_columns = {"account_id", "category_id", "category_source"}

        if columns & legacy_columns:
            connection.executescript(
                """
                PRAGMA foreign_keys = OFF;

                CREATE TABLE transactions_new (
                    transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_transaction_id TEXT,
                    transaction_date DATE NOT NULL,
                    posted_date DATE,
                    description TEXT NOT NULL,
                    merchant TEXT,
                    amount DECIMAL NOT NULL,
                    "type" TEXT CHECK (
                        "type" IS NULL
                        OR "type" IN ('EXPENSE', 'INCOME', 'TRANSFER', 'OTHER')
                    ),
                    category TEXT,
                    confidence REAL CHECK (
                        confidence IS NULL OR confidence BETWEEN 0 AND 1
                    ),
                    needs_review BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                INSERT INTO transactions_new (
                    transaction_id,
                    external_transaction_id,
                    transaction_date,
                    posted_date,
                    description,
                    merchant,
                    amount,
                    "type",
                    category,
                    confidence,
                    needs_review,
                    created_at
                )
                SELECT
                    transaction_id,
                    external_transaction_id,
                    transaction_date,
                    posted_date,
                    description,
                    merchant,
                    amount,
                    "type",
                    category,
                    confidence,
                    needs_review,
                    created_at
                FROM transactions;

                DROP TABLE transactions;
                ALTER TABLE transactions_new RENAME TO transactions;

                PRAGMA foreign_keys = ON;
                """
            )

        connection.executescript(
            """
            DROP INDEX IF EXISTS idx_transactions_account_id;
            DROP INDEX IF EXISTS idx_transactions_category_id;
            DROP TABLE IF EXISTS accounts;
            DROP TABLE IF EXISTS categories;

            CREATE INDEX IF NOT EXISTS idx_transactions_transaction_date
                ON transactions (transaction_date);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_external_id
                ON transactions (external_transaction_id)
                WHERE external_transaction_id IS NOT NULL;
            """
        )


if __name__ == "__main__":
    database = FinancialPanditDB()
    database.initialize()
    print(f"Database initialized at {database.db_path.resolve()}")
