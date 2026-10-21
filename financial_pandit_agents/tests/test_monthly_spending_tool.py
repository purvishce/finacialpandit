"""Verify the dollar report contract using synthetic transaction data."""

import argparse
import json
import sqlite3
import unittest

from tools.monthly_spending_tool import get_monthly_spending


class MonthlySpendingTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("""CREATE TABLE transactions (
            transaction_date TEXT, category TEXT, amount NUMERIC, type TEXT
        )""")
        self.db.executemany("INSERT INTO transactions VALUES (?, ?, ?, ?)", [
            ("2026-09-01", "Groceries", 390.25, "EXPENSE"),
            ("2026-09-02", "Dining", 199.9, "EXPENSE"),
            ("2026-09-03", "Dining", 0.1, "EXPENSE"),
            ("2026-09-04", "Salary", 1000, "INCOME"),
            ("2026-09-05", "Transfer", 100, "TRANSFER"),
            ("2026-10-01", None, 0.29, "EXPENSE"),
        ])

    def test_monthly_report_contains_numeric_dollar_amounts(self):
        result = get_monthly_spending(self.db, "2026-09")
        expected = {
            "month": "2026-09", "total_spending_dollars": 590.25,
            "categories": [
                {"category": "Groceries", "spending_dollars": 390.25},
                {"category": "Dining", "spending_dollars": 200.0},
            ],
        }
        self.assertEqual(result, expected)
        self.assertEqual(json.loads(json.dumps(result)), expected)

    def test_all_months_preserve_fractional_dollars(self):
        reports = get_monthly_spending(self.db)
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[1], {
            "month": "2026-10", "total_spending_dollars": 0.29,
            "categories": [{"category": "Uncategorized", "spending_dollars": 0.29}],
        })

    def test_totals_do_not_accumulate_binary_rounding_errors(self):
        self.db.execute("DELETE FROM transactions")
        self.db.executemany("INSERT INTO transactions VALUES (?, ?, ?, ?)", [
            ("2026-09-01", "A", 0.1, "EXPENSE"),
            ("2026-09-01", "B", 0.2, "EXPENSE"),
        ])
        self.assertEqual(get_monthly_spending(self.db, "2026-09")["total_spending_dollars"], 0.3)

    def test_empty_month_and_database(self):
        self.assertEqual(get_monthly_spending(self.db, "2025-01"), {
            "month": "2025-01", "total_spending_dollars": 0.0, "categories": [],
        })
        self.db.execute("DELETE FROM transactions")
        self.assertEqual(get_monthly_spending(self.db), [])

    def test_invalid_month_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            get_monthly_spending(self.db, "2026-13")


if __name__ == "__main__":
    unittest.main()
