from __future__ import annotations

import os
import argparse
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from Ingest.categories import DEFAULT_CATEGORIES, PUBLIC_KNOWLEDGE_CATEGORIES


PROJECT_ROOT = Path(__file__).resolve().parent


def load_env_file(env_path: Path = PROJECT_ROOT / ".env") -> None:
    """Load local environment settings without overriding process variables."""
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env_file()


def resolve_database_url(database_url: str | None = None) -> str:
    """Resolve the PostgreSQL URL from an explicit value, Key Vault, or local env."""
    if database_url:
        return database_url

    key_vault_url = os.environ.get("AZURE_KEY_VAULT_URL")
    if key_vault_url:
        secret_name = os.environ.get("DATABASE_URL_SECRET_NAME", "database-url")
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "Azure Key Vault is configured but its dependencies are missing. "
                "Install azure-identity and azure-keyvault-secrets."
            ) from error

        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=key_vault_url, credential=credential)
        secret_value = client.get_secret(secret_name).value
        if secret_value:
            return secret_value
        raise RuntimeError(
            f"Azure Key Vault secret '{secret_name}' in '{key_vault_url}' is empty."
        )

    local_database_url = os.environ.get("DATABASE_URL")
    if local_database_url:
        return local_database_url

    raise RuntimeError(
        "Database URL is required. Configure AZURE_KEY_VAULT_URL and the "
        "database-url secret, or set DATABASE_URL for local development."
    )


class FinancialPanditDB:
    """Create and access the Financial Pandit PostgreSQL database."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = resolve_database_url(database_url)

    def initialize(self, *, reset_legacy_transactions: bool = False) -> None:
        """Create the schema; optionally replace the incompatible legacy table.

        Run as a schema owner, separate from the restricted application role.
        """
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('transactions')")
                if cursor.fetchone()[0] is not None:
                    cursor.execute("""
                        SELECT EXISTS (
                            SELECT 1 FROM pg_attribute
                            WHERE attrelid = 'transactions'::regclass
                              AND attname = 'user_id' AND NOT attisdropped
                        )
                    """)
                    if not cursor.fetchone()[0]:
                        if not reset_legacy_transactions:
                            raise RuntimeError(
                                "Legacy transactions table found. Re-run with "
                                "--reset-legacy-transactions to delete its data and replace it."
                            )
                        cursor.execute("DROP TABLE transactions")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS categories (
                        category_id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE,
                        type TEXT NOT NULL CHECK (type IN ('EXPENSE', 'INCOME', 'TRANSFER', 'OTHER'))
                    );
                    CREATE TABLE IF NOT EXISTS public_knowledge_categories (
                        name TEXT PRIMARY KEY
                    );
                    CREATE TABLE IF NOT EXISTS application_users (
                        user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        entra_object_id UUID NOT NULL UNIQUE,
                        email TEXT,
                        display_name TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS accounts (
                        account_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        user_id UUID NOT NULL REFERENCES application_users(user_id),
                        name TEXT NOT NULL,
                        institution_name TEXT,
                        account_type TEXT,
                        account_last_four TEXT CHECK (account_last_four IS NULL OR account_last_four ~ '^[0-9]{4}$'),
                        currency_code CHAR(3) NOT NULL CHECK (currency_code ~ '^[A-Z]{3}$'),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (user_id, account_id)
                    );
                    CREATE TABLE IF NOT EXISTS statements (
                        statement_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        user_id UUID NOT NULL,
                        account_id UUID NOT NULL,
                        original_filename TEXT,
                        period_start DATE,
                        period_end DATE,
                        file_hash TEXT,
                        processing_status TEXT NOT NULL DEFAULT 'PENDING'
                            CHECK (processing_status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')),
                        error_message TEXT,
                        uploaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        processed_at TIMESTAMPTZ,
                        CHECK (period_start IS NULL OR period_end IS NULL OR period_start <= period_end),
                        FOREIGN KEY (user_id, account_id) REFERENCES accounts(user_id, account_id),
                        UNIQUE (user_id, account_id, statement_id)
                    );
                    CREATE TABLE IF NOT EXISTS transactions (
                        transaction_id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                        user_id UUID NOT NULL,
                        account_id UUID NOT NULL,
                        statement_id UUID,
                        source TEXT NOT NULL CHECK (source IN ('STATEMENT', 'MANUAL')),
                        external_transaction_id TEXT,
                        transaction_date DATE NOT NULL,
                        posted_date DATE,
                        description TEXT NOT NULL,
                        merchant TEXT,
                        amount NUMERIC(18, 2) NOT NULL,
                        currency_code CHAR(3) NOT NULL CHECK (currency_code ~ '^[A-Z]{3}$'),
                        type TEXT CHECK (
                            type IS NULL OR type IN ('EXPENSE', 'INCOME', 'TRANSFER', 'OTHER')
                        ),
                        category TEXT,
                        needs_review BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        CHECK ((source = 'MANUAL' AND statement_id IS NULL) OR
                               (source = 'STATEMENT' AND statement_id IS NOT NULL)),
                        FOREIGN KEY (user_id, account_id) REFERENCES accounts(user_id, account_id),
                        FOREIGN KEY (user_id, account_id, statement_id)
                            REFERENCES statements(user_id, account_id, statement_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts (user_id);
                    CREATE INDEX IF NOT EXISTS idx_statements_user_account ON statements (user_id, account_id);
                    CREATE INDEX IF NOT EXISTS idx_transactions_user_date
                        ON transactions (user_id, transaction_date);
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_external_id
                        ON transactions (account_id, external_transaction_id)
                        WHERE external_transaction_id IS NOT NULL;
                    """
                )
                cursor.executemany(
                    """
                    INSERT INTO categories (category_id, name, type) VALUES (%s, %s, %s)
                    ON CONFLICT (category_id) DO UPDATE SET name = EXCLUDED.name, type = EXCLUDED.type
                    """,
                    DEFAULT_CATEGORIES,
                )
                cursor.executemany(
                    """
                    INSERT INTO public_knowledge_categories (name) VALUES (%s)
                    ON CONFLICT (name) DO NOTHING
                    """,
                    [(name,) for name in PUBLIC_KNOWLEDGE_CATEGORIES],
                )
                for table, owner_column in (
                    ("application_users", "user_id"),
                    ("accounts", "user_id"),
                    ("statements", "user_id"),
                    ("transactions", "user_id"),
                ):
                    # Identifiers are constants defined above, not user input.
                    cursor.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
                    cursor.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
                    cursor.execute(f"DROP POLICY IF EXISTS user_isolation ON {table}")
                    cursor.execute(f"""
                        CREATE POLICY user_isolation ON {table}
                        USING ({owner_column} = NULLIF(current_setting('app.user_id', true), '')::uuid)
                        WITH CHECK ({owner_column} = NULLIF(current_setting('app.user_id', true), '')::uuid)
                    """)

    @staticmethod
    def set_user_context(connection: psycopg.Connection[Any], user_id: UUID | str) -> None:
        """Set trusted authenticated identity for the current transaction only.

        Never expose arbitrary SQL to untrusted callers: they could change this value.
        Call again after each commit or rollback, including on pooled connections.
        """
        connection.execute("SELECT set_config('app.user_id', %s, true)", (str(UUID(str(user_id))),))

    def connect(self) -> psycopg.Connection[Any]:
        sslmode = os.environ.get("DATABASE_SSL_MODE")
        connect_options: dict[str, Any] = {"connect_timeout": 10}
        if sslmode:
            connect_options["sslmode"] = sslmode
        return psycopg.connect(self.database_url, **connect_options)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize the PostgreSQL schema.")
    parser.add_argument(
        "--reset-legacy-transactions", action="store_true",
        help="Delete the old transactions table and its data if it lacks user ownership.",
    )
    args = parser.parse_args()
    FinancialPanditDB().initialize(reset_legacy_transactions=args.reset_legacy_transactions)
    print("PostgreSQL schema initialized.")
