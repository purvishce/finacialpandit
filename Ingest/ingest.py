from __future__ import annotations

import argparse
import csv
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATEMENTS_PATH = PROJECT_ROOT / "Statements"
DEFAULT_DB_PATH = PROJECT_ROOT / "financial_pandit.db"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import FinancialPanditDB
from Ingest.v1.decisioncall import categorize_transaction


CSV_TYPE_TO_TRANSACTION_TYPE = {
    "Purchase": "EXPENSE",
    "Credit": "INCOME",
    "Payment": "TRANSFER",
}

REQUIRED_COLUMNS = {
    "Transaction Date",
    "Post Date",
    "Type",
    "Description",
    "Reference Number",
    "Amount",
}


def parse_amount(value: str) -> str:
    cleaned = value.strip().replace("$", "").replace(",", "")

    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"

    try:
        return str(Decimal(cleaned))
    except InvalidOperation as error:
        raise ValueError(f"Invalid amount: {value!r}") from error


def normalize_blank(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = value.strip()
    return stripped or None


def validate_headers(csv_path: Path, fieldnames: Iterable[str] | None) -> None:
    actual_columns = set(fieldnames or ())
    missing_columns = REQUIRED_COLUMNS - actual_columns

    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"{csv_path} is missing required columns: {missing}")


def iter_csv_paths(path: Path) -> list[Path]:
    path = resolve_project_path(path)

    if path.is_dir():
        return sorted(path.glob("*.csv"))

    return [path]


def resolve_project_path(path: Path) -> Path:
    if path.is_absolute():
        return path

    return PROJECT_ROOT / path


def ingest_csv(connection, csv_path: Path) -> int:
    inserted_count = 0

    with csv_path.open(newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        validate_headers(csv_path, reader.fieldnames)

        for row_number, row in enumerate(reader, start=2):
            statement_type = normalize_blank(row["Type"])
            transaction_type = CSV_TYPE_TO_TRANSACTION_TYPE.get(statement_type or "")

            if transaction_type is None:
                raise ValueError(
                    f"{csv_path}:{row_number} has unsupported Type: {statement_type!r}"
                )

            description = normalize_blank(row["Description"])
            reference_number = normalize_blank(row["Reference Number"])

            if description is None:
                raise ValueError(f"{csv_path}:{row_number} has an empty Description")

            if reference_number is not None and transaction_exists(
                connection,
                reference_number,
            ):
                continue

            decision = categorize_transaction(row)

            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO transactions (
                    external_transaction_id,
                    transaction_date,
                    posted_date,
                    description,
                    merchant,
                    amount,
                    "type",
                    category,
                    confidence,
                    needs_review
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1);
                """,
                (
                    reference_number,
                    row["Transaction Date"].strip(),
                    normalize_blank(row["Post Date"]),
                    description,
                    description,
                    parse_amount(row["Amount"]),
                    transaction_type,
                    decision.category,
                    decision.confidence,
                ),
            )
            inserted_count += cursor.rowcount

    return inserted_count


def transaction_exists(connection, reference_number: str) -> bool:
    return (
        connection.execute(
            """
            SELECT 1
            FROM transactions
            WHERE external_transaction_id = ?
            LIMIT 1;
            """,
            (reference_number,),
        ).fetchone()
        is not None
    )


def ingest(path: Path, db_path: Path) -> int:
    db_path = resolve_project_path(db_path)
    database = FinancialPanditDB(db_path)
    database.initialize()

    csv_paths = iter_csv_paths(path)
    if not csv_paths:
        raise FileNotFoundError(f"No CSV files found in {path}")

    total_inserted = 0
    with database.connect() as connection:
        for csv_path in csv_paths:
            inserted = ingest_csv(connection, csv_path)
            total_inserted += inserted
            print(f"{csv_path}: inserted {inserted} row(s)")

    return total_inserted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import transaction CSV files into the Financial Pandit database."
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=DEFAULT_STATEMENTS_PATH,
        type=Path,
        help="CSV file or directory of CSV files. Defaults to the project Statements folder.",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        type=Path,
        help="SQLite database path. Defaults to the project financial_pandit.db.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    total = ingest(args.path, args.db)
    print(f"Inserted {total} transaction(s)")
