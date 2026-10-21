"""Run with unittest discovery; no model calls or API keys are needed."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from matplotlib.axes import Axes
from PIL import Image

from financial_pandit_agents.tools.spending_chart_tool import SpendingChartTool


class SpendingChartToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tool = SpendingChartTool(output_dir=Path(self.temp.name))
        self.instructions = {
            "chart_type": "bar",
            "title": "Spending by category",
            "date_range": "August and September 2026",
            "labels": ["Groceries", "Dining"],
            "series": [
                {"name": "August", "values_dollars": [300.0, 150.0]},
                {"name": "September", "values_dollars": [390.0, 200.0]},
            ],
        }

    def test_all_chart_types_produce_unique_readable_pngs(self):
        paths = []
        for chart_type in ("bar", "line", "horizontal_bar", "stacked_bar", "waterfall", "sankey"):
            instructions = {**self.instructions, "chart_type": chart_type}
            if chart_type == "waterfall":
                instructions.update(waterfall_start_dollars=450.0, series=[
                    {"name": "Change", "values_dollars": [90.0, -20.0]},
                ])
            elif chart_type == "sankey":
                instructions.update(labels=["Income", "Spending"], series=[
                    {"name": "Budget", "values_dollars": [450.0, -450.0]},
                ])
            result = json.loads(self.tool.run(**instructions))
            self.assertEqual(result["status"], "success")
            artifact = Path(result["artifact_reference"])
            self.assertEqual(artifact.parent, self.tool.output_dir.resolve())
            with Image.open(artifact) as image:
                self.assertEqual(image.format, "PNG")
                image.verify()
            paths.append(artifact)
        self.assertEqual(len(set(paths)), 6)

    def test_sankey_accepts_multiple_sources_and_allocations(self):
        result = json.loads(self.tool.run(**{
            **self.instructions, "chart_type": "sankey",
            "labels": ["Salary", "Other income", "Groceries", "Dining", "Savings"],
            "series": [{"name": "Monthly budget", "values_dollars": [1000.0, 200.0, -390.0, -200.0, -610.0]}],
        }))
        self.assertEqual(result["status"], "success")
        self.assertTrue(Path(result["artifact_reference"]).is_file())

    def test_sankey_rejects_unbalanced_or_invalid_flows_before_writing(self):
        for values in ([100.0, -99.99], [100.0, 50.0], [-100.0, -50.0], [0, 0]):
            with self.subTest(values=values):
                result = json.loads(self.tool._run(**{
                    **self.instructions, "chart_type": "sankey",
                    "series": [{"name": "Budget", "values_dollars": values}],
                }))
                self.assertEqual(result["status"], "error")
        result = json.loads(self.tool._run(**{**self.instructions, "chart_type": "sankey"}))
        self.assertEqual(result["status"], "error")
        self.assertEqual(list(self.tool.output_dir.iterdir()), [])

    def test_plots_dollar_values_without_rescaling(self):
        plotted = []
        original_bar = Axes.bar

        def record_bar(axes, positions, heights, **kwargs):
            plotted.append(heights)
            return original_bar(axes, positions, heights, **kwargs)

        with patch.object(Axes, "bar", record_bar):
            result = json.loads(self.tool.run(**self.instructions))
        self.assertEqual(result["status"], "success")
        self.assertEqual(plotted, [[300.0, 150.0], [390.0, 200.0]])

    def test_rejects_misaligned_series_before_writing(self):
        result = json.loads(self.tool._run(**{
            **self.instructions,
            "series": [{"name": "September", "values_dollars": [1.0]}],
        }))
        self.assertEqual(result["status"], "error")
        self.assertEqual(list(self.tool.output_dir.iterdir()), [])

    def test_horizontal_bars_display_currency_units_and_preserve_order(self):
        plotted = []
        original_barh = Axes.barh

        def record_barh(axes, positions, widths, **kwargs):
            plotted.append(widths)
            return original_barh(axes, positions, widths, **kwargs)

        with patch.object(Axes, "barh", record_barh):
            result = json.loads(self.tool.run(**{
                **self.instructions, "chart_type": "horizontal_bar",
            }))
        self.assertEqual(result["status"], "success")
        self.assertEqual(plotted, [[300.0, 150.0], [390.0, 200.0]])

    def test_stacks_positive_and_negative_values_separately(self):
        plotted = []
        original_bar = Axes.bar

        def record_bar(axes, positions, heights, **kwargs):
            plotted.append((heights, kwargs["bottom"]))
            return original_bar(axes, positions, heights, **kwargs)

        with patch.object(Axes, "bar", record_bar):
            result = json.loads(self.tool.run(**{
                **self.instructions, "chart_type": "stacked_bar",
                "series": [
                    {"name": "Food", "values_dollars": [100.0, -20.0]},
                    {"name": "Other", "values_dollars": [30.0, -10.0]},
                    {"name": "Travel", "values_dollars": [-5.0, 40.0]},
                ],
            }))
        self.assertEqual(result["status"], "success")
        self.assertEqual(plotted, [
            ([100.0, -20.0], [0.0, 0.0]),
            ([30.0, -10.0], [100.0, -20.0]),
            ([-5.0, 40.0], [0.0, 0.0]),
        ])

    def test_waterfall_reconciles_total_and_positions_decreases(self):
        plotted = []
        original_bar = Axes.bar

        def record_bar(axes, position, height, **kwargs):
            plotted.append((height, kwargs.get("bottom", 0)))
            return original_bar(axes, position, height, **kwargs)

        for start in (100.0, -100.0):
            with self.subTest(start=start):
                plotted.clear()
                with patch.object(Axes, "bar", record_bar):
                    result = json.loads(self.tool.run(**{
                        **self.instructions, "chart_type": "waterfall",
                        "waterfall_start_dollars": start,
                        "series": [{"name": "Change", "values_dollars": [50.0, -20.0]}],
                    }))
                self.assertEqual(result["status"], "success")
                self.assertEqual(plotted, [
                    (start, 0), (50.0, start),
                    (20.0, (start + 30)), ((start + 30), 0),
                ])

    def test_waterfall_requires_explicit_baseline_and_one_series(self):
        for changes in (
            {"chart_type": "waterfall"},
            {"chart_type": "waterfall", "waterfall_start_dollars": 100.0},
            {"waterfall_start_dollars": 100.0},
        ):
            with self.subTest(changes=changes):
                result = json.loads(self.tool._run(**{**self.instructions, **changes}))
                self.assertEqual(result["status"], "error")

    def test_rejects_excess_precision_and_unsupported_chart(self):
        for changes in (
            {"series": [{"name": "September", "values_dollars": [0.015, 0.02]}]},
            {"chart_type": "pie"},
            {"labels": ["Dining", "Dining"]},
            {"chart_type": "line", "labels": ["September"],
             "series": [{"name": "Spending", "values_dollars": [1.0]}]},
        ):
            with self.subTest(changes=changes):
                result = json.loads(self.tool._run(**{**self.instructions, **changes}))
                self.assertEqual(result["status"], "error")

    def test_write_failure_returns_error_without_artifact(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError("Cannot write chart")):
            result = json.loads(self.tool.run(**self.instructions))
        self.assertEqual(result["status"], "error")
        self.assertNotIn("artifact_reference", result)

    def test_fractional_dollars_are_plotted_directly(self):
        plotted = []
        original_bar = Axes.bar

        def record_bar(axes, positions, heights, **kwargs):
            plotted.append(heights)
            return original_bar(axes, positions, heights, **kwargs)

        with patch.object(Axes, "bar", record_bar):
            result = json.loads(self.tool.run(**{
                **self.instructions,
                "series": [{"name": "September", "values_dollars": [390.25, 0.29]}],
            }))
        self.assertEqual(result["status"], "success")
        self.assertEqual(plotted, [[390.25, 0.29]])

    def test_sankey_balances_fractional_dollars_exactly(self):
        result = json.loads(self.tool.run(**{
            **self.instructions, "chart_type": "sankey",
            "labels": ["Income", "Food", "Savings"],
            "series": [{"name": "Budget", "values_dollars": [0.3, -0.1, -0.2]}],
        }))
        self.assertEqual(result["status"], "success")

    def test_waterfall_accumulates_fractional_dollars_exactly(self):
        heights = []
        original_bar = Axes.bar

        def record_bar(axes, position, height, **kwargs):
            heights.append(height)
            return original_bar(axes, position, height, **kwargs)

        with patch.object(Axes, "bar", record_bar):
            result = json.loads(self.tool.run(**{
                **self.instructions, "chart_type": "waterfall",
                "waterfall_start_dollars": 0.1,
                "series": [{"name": "Change", "values_dollars": [0.1, 0.1]}],
            }))
        self.assertEqual(result["status"], "success")
        self.assertEqual(heights[-1], 0.3)

    def test_rejects_nonfinite_and_boolean_dollar_values(self):
        for value in (float("nan"), float("inf"), True):
            with self.subTest(value=value):
                result = json.loads(self.tool._run(**{
                    **self.instructions,
                    "series": [{"name": "Invalid", "values_dollars": [value, 1]}],
                }))
                self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
