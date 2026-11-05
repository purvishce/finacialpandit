"""Read-only transaction tools backed by PostgreSQL.

The public class names remain compatible with the existing CrewAI wiring.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import closing

import psycopg
from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field


SQLValue = str | int | float | None


class SQLiteQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=20000)
    parameters: list[SQLValue] | dict[str, SQLValue] = Field(default_factory=list)
    max_rows: int = Field(default=200, ge=1, le=1000)


def _connect() -> psycopg.Connection:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for transaction tools.")
    return psycopg.connect(database_url, connect_timeout=5)


def _normalize_placeholders(query: str) -> str:
    """Keep compatibility with older agent-generated SQLite '?' parameters."""
    return re.sub(r"\?(?![0-9])", "%s", query)


def _validate_read_only(query: str) -> None:
    stripped = query.strip().rstrip(";").strip()
    if not re.match(r"^(SELECT|WITH)\b", stripped, re.IGNORECASE):
        raise ValueError("Only one read-only SELECT or WITH query is allowed.")
    if ";" in stripped:
        raise ValueError("Multiple SQL statements are not allowed.")
    if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|CALL|DO)\b", stripped, re.IGNORECASE):
        raise ValueError("Write and administrative SQL is not allowed.")


class SQLiteQueryTool(BaseTool):
    name: str = "query_transactions"
    description: str = (
        "Execute one read-only PostgreSQL SELECT or WITH query against transactions. "
        "Inspect the schema first. Use %s or named placeholders and bound parameters. "
        "The amount column is already in dollars; preserve financial precision. "
        "Results are capped at max_rows. Database changes are blocked."
    )
    args_schema: type[BaseModel] = SQLiteQueryInput
    query_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    def _run(self, query: str, parameters: list | dict | None = None, max_rows: int = 200) -> str:
        try:
            spec = SQLiteQueryInput.model_validate({
                "query": query, "parameters": parameters if parameters is not None else [], "max_rows": max_rows,
            })
            _validate_read_only(spec.query)
            with closing(_connect()) as connection:
                with connection.cursor() as cursor:
                    timeout_ms = int(self.query_timeout_seconds * 1000)
                    cursor.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
                    cursor.execute(_normalize_placeholders(spec.query), spec.parameters)
                    if cursor.description is None:
                        raise ValueError("The query must return rows.")
                    columns = [column.name for column in cursor.description]
                    rows = cursor.fetchmany(spec.max_rows + 1)
            truncated = len(rows) > spec.max_rows
            rows = rows[:spec.max_rows]
            serializable_rows = [list(row) for row in rows]
            return json.dumps({
                "status": "success" if rows else "empty", "columns": columns,
                "rows": serializable_rows, "returned_row_count": len(rows),
                "truncated": truncated, "amount_unit": "dollars",
                "query": {"sql": spec.query, "parameters": spec.parameters},
                "limitations": [f"Results limited to {spec.max_rows} rows."] if truncated else [],
            }, default=str, allow_nan=False)
        except (psycopg.Error, ValueError, TypeError, OverflowError) as error:
            return json.dumps({"status": "error", "error": str(error)})


class SQLiteSchemaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SQLiteSchemaTool(BaseTool):
    name: str = "inspect_transaction_schema"
    description: str = "Inspect the PostgreSQL transactions schema. Amount is in dollars."
    args_schema: type[BaseModel] = SQLiteSchemaInput

    def _run(self) -> str:
        try:
            with closing(_connect()) as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT column_name, data_type, is_nullable, ordinal_position
                    FROM information_schema.columns
                    WHERE table_schema = current_schema() AND table_name = 'transactions'
                    ORDER BY ordinal_position
                    """
                )
                columns = cursor.fetchall()
            if not columns:
                raise ValueError("The transactions table was not found.")
            return json.dumps({
                "status": "success", "table": "transactions", "amount_unit": "dollars",
                "columns": [{"name": row[0], "type": row[1], "not_null": row[2] == "NO"} for row in columns],
            })
        except (psycopg.Error, ValueError) as error:
            return json.dumps({"status": "error", "error": str(error)})
