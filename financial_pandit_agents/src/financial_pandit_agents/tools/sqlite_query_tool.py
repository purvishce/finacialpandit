"""Read-only transaction tools for the Transaction Analyst."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from time import monotonic

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field


DEFAULT_DB_PATH = Path(__file__).resolve().parents[4] / "financial_pandit.db"
SQLValue = str | int | float | None


class SQLiteQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1, max_length=20000,
        description="One read-only SELECT or WITH query against transactions.",
    )
    parameters: list[SQLValue] | dict[str, SQLValue] = Field(
        default_factory=list,
        description="Bound values: a list for ? placeholders, or an object for :name placeholders.",
    )
    max_rows: int = Field(default=200, ge=1, le=1000)


def _open_read_only(db_path: Path, timeout: float) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=timeout)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
    except sqlite3.Error:
        connection.close()
        raise
    return connection


def _authorize(action: int, arg1: str | None, arg2: str | None,
               database: str | None, source: str | None) -> int:
    """Allow reads and known analytical functions; reject other SQLite actions."""
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        # SQLite can omit the database for implicit rowid/count reads.
        if database in ("main", None) and arg1 in ("transactions", "sqlite_master", "sqlite_schema"):
            return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        allowed = {
            "abs", "avg", "coalesce", "count", "date", "datetime", "dense_rank",
            "first_value", "ifnull", "instr", "julianday", "lag", "last_value",
            "lead", "length", "like", "lower", "ltrim", "max", "min", "nullif",
            "rank", "replace", "round", "row_number", "rtrim", "strftime",
            "substr", "substring", "sum", "time", "total", "trim", "typeof",
            "unixepoch", "upper",
        }
        if (arg2 or "").lower() in allowed:
            return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class SQLiteQueryTool(BaseTool):
    name: str = "query_transactions"
    description: str = (
        "Execute one read-only SQLite SELECT or WITH query against transactions. "
        "Inspect the schema with inspect_transaction_schema first. Bind user values "
        "using parameters instead of embedding them in SQL. The amount column is "
        "already in dollars: use amount AS amount_dollars or "
        "ROUND(SUM(amount), 2) AS spending_dollars. Distinguish EXPENSE, INCOME, "
        "and TRANSFER. Include ORDER BY for predictable row limits. Returns JSON "
        "with columns, rows, returned_row_count, truncation, and executed query. "
        "A row cap applies only to returned results, not aggregate input rows. "
        "Database changes, attachments, and arbitrary functions are blocked."
    )
    args_schema: type[BaseModel] = SQLiteQueryInput
    db_path: Path = Field(default=DEFAULT_DB_PATH, description="Application-configured database path.")
    query_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    def _run(self, query: str, parameters: list | dict | None = None,
             max_rows: int = 200) -> str:
        try:
            spec = SQLiteQueryInput.model_validate({
                "query": query, "parameters": parameters if parameters is not None else [],
                "max_rows": max_rows,
            })
            with closing(_open_read_only(self.db_path, self.query_timeout_seconds)) as connection:
                deadline = monotonic() + self.query_timeout_seconds
                connection.set_authorizer(_authorize)
                connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
                cursor = connection.execute(spec.query, spec.parameters)
                if cursor.description is None:
                    raise ValueError("The query must return rows.")
                columns = [column[0] for column in cursor.description]
                rows = cursor.fetchmany(spec.max_rows + 1)
                truncated = len(rows) > spec.max_rows
                rows = rows[:spec.max_rows]
            return json.dumps({
                "status": "success" if rows else "empty",
                "columns": columns, "rows": rows, "returned_row_count": len(rows),
                "truncated": truncated, "amount_unit": "dollars",
                "query": {"sql": spec.query, "parameters": spec.parameters},
                "limitations": [f"Results limited to {spec.max_rows} rows."] if truncated else [],
            }, allow_nan=False)
        except (sqlite3.Error, ValueError, TypeError, OverflowError) as error:
            return json.dumps({"status": "error", "error": str(error)})


class SQLiteSchemaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SQLiteSchemaTool(BaseTool):
    name: str = "inspect_transaction_schema"
    description: str = (
        "Inspect the transactions table before writing SQL. Returns column names, "
        "SQLite types, nullability, and primary keys as JSON. Amount is in dollars. "
        "No inputs are required; this tool does not retrieve transaction records."
    )
    args_schema: type[BaseModel] = SQLiteSchemaInput
    db_path: Path = Field(default=DEFAULT_DB_PATH, description="Application-configured database path.")

    def _run(self) -> str:
        try:
            with closing(_open_read_only(self.db_path, 5.0)) as connection:
                columns = connection.execute("PRAGMA table_info(transactions)").fetchall()
            if not columns:
                raise ValueError("The transactions table was not found.")
            return json.dumps({
                "status": "success", "table": "transactions", "amount_unit": "dollars",
                "columns": [
                    {"name": row[1], "type": row[2], "not_null": bool(row[3]),
                     "primary_key": bool(row[5])}
                    for row in columns
                ],
            })
        except (sqlite3.Error, ValueError) as error:
            return json.dumps({"status": "error", "error": str(error)})
