from __future__ import annotations

import argparse
import json
import os
import re
from decimal import Decimal
from typing import TypedDict

from database import FinancialPanditDB


MONTHLY_SPENDING_SQL = """
    SELECT to_char(transaction_date, 'YYYY-MM') AS transaction_month,
           COALESCE(NULLIF(BTRIM(category), ''), 'Uncategorized') AS spending_category,
           ROUND(SUM(amount), 2) AS spending_dollars
    FROM transactions
    WHERE type = 'EXPENSE'
      AND (%s::text IS NULL OR to_char(transaction_date, 'YYYY-MM') = %s)
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
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value) or value[:4] == "0000":
        raise argparse.ArgumentTypeError("Month must use YYYY-MM, for example 2026-09.")
    return value


def get_monthly_spending(connection, month: str | None = None) -> MonthlySpending | list[MonthlySpending]:
    if month is not None:
        parse_month(month)
    with connection.cursor() as cursor:
        cursor.execute(MONTHLY_SPENDING_SQL, (month, month))
        rows = cursor.fetchall()
    reports: dict[str, MonthlySpending] = {}
    totals: dict[str, Decimal] = {}
    if month is not None:
        reports[month] = {"month": month, "total_spending_dollars": 0.0, "categories": []}
    for transaction_month, category, spending_dollars in rows:
        if transaction_month is None:
            raise ValueError("An expense has an invalid transaction date.")
        reports.setdefault(transaction_month, {
            "month": transaction_month, "total_spending_dollars": 0.0, "categories": [],
        })
        reports[transaction_month]["categories"].append({
            "category": category, "spending_dollars": float(spending_dollars),
        })
        totals[transaction_month] = totals.get(transaction_month, Decimal("0")) + Decimal(str(spending_dollars))
        reports[transaction_month]["total_spending_dollars"] = float(totals[transaction_month].quantize(Decimal("0.01")))
    return reports[month] if month is not None else list(reports.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="Show monthly spending by category as JSON with amounts in dollars.")
    parser.add_argument("--month", type=parse_month, help="Filter to YYYY-MM.")
    args = parser.parse_args()
    try:
        with FinancialPanditDB(os.environ.get("DATABASE_URL")).connect() as connection:
            spending = get_monthly_spending(connection, args.month)
    except Exception as error:
        parser.exit(1, f"Unable to read spending: {error}\n")
    print(json.dumps(spending, indent=2))


if __name__ == "__main__":
    main()
