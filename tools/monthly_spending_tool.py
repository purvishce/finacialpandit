from __future__ import annotations

import argparse
import json
import re
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import TypedDict


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "financial_pandit.db"

MONTHLY_SPENDING_SQL = """
    SELECT strftime('%Y-%m', transaction_date) AS transaction_month,
           COALESCE(NULLIF(TRIM(category), ''), 'Uncategorized') AS spending_category,
           ROUND(SUM(amount), 2) AS spending_dollars
    FROM transactions
    WHERE "type" = 'EXPENSE'
      AND (? IS NULL OR strftime('%Y-%m', transaction_date) = ?)
    GROUP BY transaction_month, spending_category
    ORDER BY transaction_month, spending_dollars DESC, spending_category
"""


class CategorySpending(TypedDict):
    category: str
    spending_dollars: float


class MonthlySpending(TypedDict):
    month: str
    total_spending_dollars: float
    categories: list[CategorySpending]


def parse_month(value: str) -> str:
    """Validate a calendar month in YYYY-MM format."""
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value) or value[:4] == "0000":
        raise argparse.ArgumentTypeError("Month must use YYYY-MM, for example 2026-09.")
    return value


def get_monthly_spending(
    connection: sqlite3.Connection, month: str | None = None
) -> MonthlySpending | list[MonthlySpending]:
    """Return one month's report, or a list of reports when month is omitted."""
    if month is not None:
        parse_month(month)
    rows = connection.execute(MONTHLY_SPENDING_SQL, (month, month))
    reports: dict[str, MonthlySpending] = {}
    totals: dict[str, Decimal] = {}
    if month is not None:
        reports[month] = {"month": month, "total_spending_dollars": 0.0, "categories": []}
    for transaction_month, category, spending_dollars in rows:
        if transaction_month is None:
            raise ValueError("An expense has an invalid transaction date.")
        if transaction_month not in reports:
            reports[transaction_month] = {
                "month": transaction_month,
                "total_spending_dollars": 0.0,
                "categories": [],
            }
        report = reports[transaction_month]
        report["categories"].append({
            "category": category,
            "spending_dollars": spending_dollars,
        })
        totals[transaction_month] = (
            totals.get(transaction_month, Decimal("0")) + Decimal(str(spending_dollars))
        )
        report["total_spending_dollars"] = float(
            totals[transaction_month].quantize(Decimal("0.01"))
        )
    return reports[month] if month is not None else list(reports.values())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show monthly spending by category as JSON with amounts in dollars."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--month", type=parse_month, help="Filter to YYYY-MM.")
    args = parser.parse_args()

    try:
        # Read-only mode avoids accidentally creating or modifying a database.
        with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            spending = get_monthly_spending(connection, args.month)
    except (sqlite3.Error, ValueError) as error:
        parser.exit(1, f"Unable to read spending: {error}\n")

    print(json.dumps(spending, indent=2))


if __name__ == "__main__":
    main()
