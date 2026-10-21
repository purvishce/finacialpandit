"""Exercise transaction tools against a temporary database, without model calls."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from financial_pandit_agents.tools.sqlite_query_tool import SQLiteQueryTool, SQLiteSchemaTool


class SQLiteQueryToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "transactions.db"
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("""CREATE TABLE transactions (
                transaction_id INTEGER PRIMARY KEY,
                transaction_date TEXT, description TEXT, amount DECIMAL,
                type TEXT, category TEXT
            )""")
            connection.executemany("""INSERT INTO transactions
                (transaction_date, description, amount, type, category)
                VALUES (?, ?, ?, ?, ?)""", [
                    ("2026-09-01", "Market", 390.25, "EXPENSE", "Groceries"),
                    ("2026-09-02", "Cafe", 20.10, "EXPENSE", "Dining"),
                    ("2026-09-03", "Salary", 1000, "INCOME", "Salary"),
                ])
            connection.commit()
        self.tool = SQLiteQueryTool(db_path=self.path)

    def test_schema_exposes_actual_columns_and_dollar_unit(self):
        result = json.loads(SQLiteSchemaTool(db_path=self.path).run())
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["amount_unit"], "dollars")
        self.assertIn("amount", [column["name"] for column in result["columns"]])

    def test_query_returns_verified_dollar_totals(self):
        result = json.loads(self.tool.run(
            query="SELECT ROUND(SUM(amount), 2) AS spending_dollars FROM transactions WHERE type = ?",
            parameters=["EXPENSE"],
        ))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["columns"], ["spending_dollars"])
        self.assertEqual(result["rows"], [[410.35]])
        self.assertFalse(result["truncated"])

    def test_named_parameters_and_cte(self):
        result = json.loads(self.tool.run(
            query="""WITH expenses AS (SELECT * FROM transactions WHERE type = :kind)
                SELECT amount AS amount_dollars FROM expenses WHERE category = :category""",
            parameters={"kind": "EXPENSE", "category": "Groceries"},
        ))
        self.assertEqual(result["rows"], [[390.25]])

    def test_bound_user_text_is_not_executed_as_sql(self):
        result = json.loads(self.tool.run(
            query="SELECT description FROM transactions WHERE category = ?",
            parameters=["Groceries' OR 1=1 --"],
        ))
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["rows"], [])

    def test_result_limit_reports_truncation(self):
        result = json.loads(self.tool.run(
            query="SELECT transaction_id FROM transactions ORDER BY transaction_id",
            max_rows=2,
        ))
        self.assertEqual(result["returned_row_count"], 2)
        self.assertTrue(result["truncated"])
        self.assertTrue(result["limitations"])

    def test_dangerous_statements_are_rejected_and_database_unchanged(self):
        before = self.path.read_bytes()
        for query in (
            "DELETE FROM transactions", "UPDATE transactions SET amount = 0",
            "DROP TABLE transactions", "CREATE TABLE unwanted (value TEXT)",
            "PRAGMA query_only = OFF", "PRAGMA writable_schema = ON",
            "ATTACH DATABASE ':memory:' AS other", "VACUUM",
            "SELECT load_extension('not-a-library')", "SELECT randomblob(1000000)",
            "SELECT 1; DELETE FROM transactions",
        ):
            with self.subTest(query=query):
                result = json.loads(self.tool.run(query=query))
                self.assertEqual(result["status"], "error")
        self.assertEqual(self.path.read_bytes(), before)

    def test_query_timeout_interrupts_unbounded_recursion(self):
        tool = SQLiteQueryTool(db_path=self.path, query_timeout_seconds=0.01)
        result = json.loads(tool.run(query="""WITH RECURSIVE numbers(x) AS (
            SELECT 1 UNION ALL SELECT x + 1 FROM numbers
        ) SELECT SUM(x) FROM numbers"""))
        self.assertEqual(result["status"], "error")
        self.assertIn("interrupted", result["error"])

    def test_missing_database_is_not_created(self):
        missing = Path(self.temp.name) / "missing.db"
        for tool in (SQLiteQueryTool(db_path=missing), SQLiteSchemaTool(db_path=missing)):
            result = json.loads(tool.run(query="SELECT 1") if isinstance(tool, SQLiteQueryTool) else tool.run())
            self.assertEqual(result["status"], "error")
        self.assertFalse(missing.exists())

    def test_invalid_query_or_parameters_return_errors(self):
        for args in (
            {"query": "SELECT missing_column FROM transactions"},
            {"query": "SELECT ?", "parameters": []},
            {"query": "SELECT 1", "max_rows": 0},
        ):
            with self.subTest(args=args):
                self.assertEqual(json.loads(self.tool._run(**args))["status"], "error")


if __name__ == "__main__":
    unittest.main()
