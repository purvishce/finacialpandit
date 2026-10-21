"""Render verified spending data using instructions from the insights agent."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, model_validator


DollarAmount = Annotated[Decimal, Field(decimal_places=2, max_digits=16, allow_inf_nan=False)]


class SpendingSeries(BaseModel):
    """One named series of dollar amounts, in the same order as the labels."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    values_dollars: list[DollarAmount] = Field(
        min_length=1, max_length=100,
        description="Dollar amounts with at most two decimal places, e.g. 390.25.",
    )


class SpendingChartInput(BaseModel):
    """Structured chart instructions; no executable code is accepted."""

    model_config = ConfigDict(extra="forbid")

    chart_type: Literal["bar", "line", "horizontal_bar", "stacked_bar", "waterfall", "sankey"] = Field(
        description=(
            "bar: grouped comparisons; line: ordered time trends; horizontal_bar: "
            "ranked categories; stacked_bar: composition by period; waterfall: "
            "a starting total plus signed category changes leading to an ending total; "
            "sankey: balanced incoming sources flowing through a total into allocations."
        )
    )
    title: str = Field(min_length=1, max_length=160)
    date_range: str = Field(
        min_length=1, max_length=100, description="Explicit period covered by the data."
    )
    labels: list[str] = Field(
        min_length=1, max_length=100,
        description="Unique categories or chronological periods, in plotting order.",
    )
    series: list[SpendingSeries] = Field(
        min_length=1, max_length=6,
        description=(
            "Verified monetary series, each with one dollar value per label. "
            "For sankey, supply exactly one series: positive values are incoming "
            "sources, negative values are outgoing allocations; their sum must be zero."
        ),
    )
    x_label: str = Field(default="Category", min_length=1, max_length=100)
    currency: str = Field(
        default="USD", pattern=r"^[A-Z]{3}$",
        description="Currency code from the source data; do not combine currencies.",
    )
    waterfall_start_dollars: DollarAmount | None = Field(
        default=None,
        description=(
            "Required only for waterfall: verified starting total in dollars. "
            "Supply exactly one series of signed changes, not absolute totals. "
            "Include all changes to reconcile the ending total."
        ),
    )

    @model_validator(mode="after")
    def validate_series(self) -> SpendingChartInput:
        if any(not label.strip() or len(label) > 100 for label in self.labels):
            raise ValueError("Labels must contain 1 to 100 nonblank characters.")
        if len(set(self.labels)) != len(self.labels):
            raise ValueError("Labels must be unique; aggregate duplicate labels first.")
        if len({series.name for series in self.series}) != len(self.series):
            raise ValueError("Series names must be unique.")
        if any(len(series.values_dollars) != len(self.labels) for series in self.series):
            raise ValueError("Each series must have exactly one value per label.")
        if self.chart_type == "line" and len(self.labels) < 2:
            raise ValueError("A trend chart requires at least two periods.")
        if self.chart_type == "waterfall":
            if self.waterfall_start_dollars is None or len(self.series) != 1:
                raise ValueError("Waterfall requires a starting total and exactly one change series.")
        elif self.waterfall_start_dollars is not None:
            raise ValueError("waterfall_start_dollars is only valid for waterfall charts.")
        if self.chart_type == "sankey":
            if len(self.series) != 1:
                raise ValueError("Sankey requires exactly one series of signed flows.")
            flows = self.series[0].values_dollars
            if not any(value > 0 for value in flows) or not any(value < 0 for value in flows):
                raise ValueError("Sankey requires incoming and outgoing flows.")
            if any(value == 0 for value in flows) or sum(flows) != 0:
                raise ValueError("Sankey flows must be nonzero and balance exactly in dollars.")
        return self


class SpendingChartTool(BaseTool):
    """Create a PNG artifact without querying data or invoking an LLM."""

    name: str = "create_spending_chart"
    description: str = (
        "Create bar, line, horizontal_bar, stacked_bar, waterfall, or sankey charts "
        "from verified financial findings. "
        "Supply title, date_range, ordered labels, and named series of dollar amounts "
        "in values_dollars (e.g. 390.25), with at most two decimal places. "
        "Bar charts compare categories or periods; line charts "
        "show time trends. Horizontal bars show rankings in the supplied order. "
        "Stacked bars show series contributions per label. Waterfall requires "
        "waterfall_start_dollars and one series of signed changes; start and end "
        "totals are drawn automatically. Sankey requires exactly one series of "
        "balanced signed flows: positive incoming sources and negative outgoing "
        "allocations. Include verified savings or unallocated funds explicitly "
        "when relevant; never invent a balancing flow. Amounts are displayed in currency units. Returns "
        "JSON with status and a local PNG artifact_reference, or an error. "
        "Use only retrieved or calculated data; this tool does not infer insights."
    )
    args_schema: type[BaseModel] = SpendingChartInput
    output_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[3] / "output" / "charts",
        description="Application-configured artifact directory, not an agent input.",
    )

    def _run(
        self,
        chart_type: str,
        title: str,
        date_range: str,
        labels: list[str],
        series: list[dict | SpendingSeries],
        x_label: str = "Category",
        currency: str = "USD",
        waterfall_start_dollars: Decimal | float | int | str | None = None,
    ) -> str:
        try:
            spec = SpendingChartInput.model_validate({
                "chart_type": chart_type, "title": title, "date_range": date_range,
                "labels": labels, "series": series, "x_label": x_label,
                "currency": currency,
                "waterfall_start_dollars": waterfall_start_dollars,
            })
            artifact = self._render(spec)
            return json.dumps({
                "status": "success", "title": spec.title,
                "chart_type": spec.chart_type, "date_range": spec.date_range,
                "currency": spec.currency, "artifact_reference": str(artifact),
                "media_type": "image/png",
            })
        except (ValueError, OSError, ImportError) as error:
            return json.dumps({"status": "error", "error": str(error)})

    def _render(self, spec: SpendingChartInput) -> Path:
        if spec.chart_type == "sankey":
            return self._render_sankey(spec)
        # A noninteractive canvas works in FastAPI without a GUI or pyplot state.
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.ticker import StrMethodFormatter

        horizontal = spec.chart_type == "horizontal_bar"
        figure = Figure(
            figsize=(11, max(6, len(spec.labels) * 0.45)) if horizontal else
                    (max(9, min(18, len(spec.labels) * 0.7)), 6),
            layout="constrained",
        )
        FigureCanvasAgg(figure)
        try:
            axes = figure.subplots()
            positions = list(range(len(spec.labels)))
            colors = ("#2563eb", "#ea580c", "#0f766e", "#9333ea", "#be123c", "#475569")
            width = 0.8 / len(spec.series)
            chart_labels = spec.labels
            if spec.chart_type == "waterfall":
                self._draw_waterfall(axes, spec)
                chart_labels = ["Start", *spec.labels, "End"]
                positions = list(range(len(chart_labels)))
            else:
                positive_dollars = [Decimal("0")] * len(spec.labels)
                negative_dollars = [Decimal("0")] * len(spec.labels)
                for index, series in enumerate(spec.series):
                    amounts = [float(dollars) for dollars in series.values_dollars]
                    offset = (index - (len(spec.series) - 1) / 2) * width
                    if spec.chart_type == "bar":
                        axes.bar([position + offset for position in positions], amounts,
                                 width=width, label=series.name, color=colors[index])
                    elif horizontal:
                        axes.barh([position + offset for position in positions], amounts,
                                  height=width, label=series.name, color=colors[index])
                    elif spec.chart_type == "stacked_bar":
                        # Separate positive and negative stacks so credits remain visible.
                        bottoms = []
                        for point, dollars in enumerate(series.values_dollars):
                            stack = positive_dollars if dollars >= 0 else negative_dollars
                            bottoms.append(float(stack[point]))
                            stack[point] += dollars
                        axes.bar(positions, amounts, bottom=bottoms, width=0.8,
                                 label=series.name, color=colors[index])
                    else:
                        axes.plot(positions, amounts, marker="o", label=series.name,
                                  color=colors[index], linewidth=2)
            if horizontal:
                axes.set_yticks(positions, chart_labels)
                axes.invert_yaxis()
                axes.set_xlabel(f"Amount ({spec.currency})")
                axes.set_ylabel(spec.x_label)
                axes.xaxis.set_major_formatter(StrMethodFormatter("{x:,.2f}"))
                axes.grid(axis="x", alpha=0.25)
                axes.axvline(0, color="#64748b", linewidth=0.7)
            else:
                axes.set_xticks(positions, chart_labels,
                                rotation=35 if len(chart_labels) > 5 else 0,
                                ha="right" if len(chart_labels) > 5 else "center")
                axes.set_xlabel(spec.x_label)
                axes.set_ylabel(f"Amount ({spec.currency})")
                axes.yaxis.set_major_formatter(StrMethodFormatter("{x:,.2f}"))
                axes.grid(axis="y", alpha=0.25)
                axes.axhline(0, color="#64748b", linewidth=0.7)
            axes.set_title(f"{spec.title}\n{spec.date_range}", pad=16)
            axes.set_axisbelow(True)
            nonnegative = all(value >= 0 for series in spec.series for value in series.values_dollars)
            if nonnegative and (spec.waterfall_start_dollars is None or spec.waterfall_start_dollars >= 0):
                if horizontal:
                    axes.set_xlim(left=0)
                else:
                    axes.set_ylim(bottom=0)
            if spec.chart_type != "waterfall":
                axes.legend()
            self.output_dir.mkdir(parents=True, exist_ok=True)
            artifact = (self.output_dir / f"spending_{uuid4().hex}.png").resolve()
            figure.savefig(artifact, format="png", dpi=150)
            return artifact
        finally:
            figure.clear()

    def _render_sankey(self, spec: SpendingChartInput) -> Path:
        """Show sources pooling into one total, then flowing into allocations."""
        from textwrap import fill

        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.patches import PathPatch, Rectangle
        from matplotlib.path import Path as PlotPath

        flows = list(zip(spec.labels, spec.series[0].values_dollars))
        sources = [(label, value) for label, value in flows if value > 0]
        destinations = [(label, -value) for label, value in flows if value < 0]
        total = sum(value for _, value in sources)
        count = max(len(sources), len(destinations))
        figure = Figure(figsize=(14, max(6, count * 0.65)), layout="constrained")
        FigureCanvasAgg(figure)
        try:
            axes = figure.subplots()
            total_height = 0.65
            gap = min(0.04, 0.25 / max(count - 1, 1))
            center_bottom = (1 - total_height) / 2
            colors = ("#ea580c", "#9333ea", "#0f766e", "#be123c", "#475569")

            def ribbon(left, right, left_y, right_y, height, color):
                middle = (left + right) / 2
                vertices = [
                    (left, left_y), (middle, left_y), (middle, right_y), (right, right_y),
                    (right, right_y + height), (middle, right_y + height),
                    (middle, left_y + height), (left, left_y + height), (left, left_y),
                ]
                codes = [PlotPath.MOVETO, PlotPath.CURVE4, PlotPath.CURVE4,
                         PlotPath.CURVE4, PlotPath.LINETO, PlotPath.CURVE4,
                         PlotPath.CURVE4, PlotPath.CURVE4, PlotPath.CLOSEPOLY]
                axes.add_patch(PathPatch(PlotPath(vertices, codes), facecolor=color,
                                         edgecolor="none", alpha=0.4))

            for incoming, entries in ((True, sources), (False, destinations)):
                top = (1 + total_height + gap * (len(entries) - 1)) / 2
                center_top = center_bottom + total_height
                for index, (label, dollars) in enumerate(entries):
                    height = total_height * float(dollars / total)
                    bottom = top - height
                    center_y = center_top - height
                    color = "#0f766e" if incoming else colors[index % len(colors)]
                    if incoming:
                        ribbon(0.08, 0.48, bottom, center_y, height, color)
                        node_x, text_x, align = 0.06, 0.04, "right"
                    else:
                        ribbon(0.52, 0.92, center_y, bottom, height, color)
                        node_x, text_x, align = 0.92, 0.96, "left"
                    axes.add_patch(Rectangle((node_x, bottom), 0.02, height,
                                             facecolor=color, edgecolor="none"))
                    axes.text(text_x, bottom + height / 2,
                              f"{fill(label, 24)}\n{dollars:,.2f} {spec.currency}",
                              ha=align, va="center", fontsize=10)
                    top = bottom - gap
                    center_top = center_y

            axes.add_patch(Rectangle((0.48, center_bottom), 0.04, total_height,
                                     facecolor="#2563eb", edgecolor="none"))
            axes.text(0.5, center_bottom + total_height + 0.05,
                      f"{fill(spec.series[0].name, 24)}\n{total:,.2f} {spec.currency}",
                      ha="center", va="bottom", fontsize=11)
            axes.set_xlim(-0.42, 1.42)
            axes.set_ylim(-0.05, 1.15)
            axes.set_axis_off()
            axes.set_title(f"{spec.title}\n{spec.date_range}", pad=16)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            artifact = (self.output_dir / f"spending_{uuid4().hex}.png").resolve()
            figure.savefig(artifact, format="png", dpi=150)
            return artifact
        finally:
            figure.clear()

    @staticmethod
    def _draw_waterfall(axes, spec: SpendingChartInput) -> None:
        """Accumulate decimal dollar changes; convert only plotted coordinates."""
        from matplotlib.patches import Patch

        running_dollars = spec.waterfall_start_dollars
        assert running_dollars is not None  # Guaranteed by input validation.
        axes.bar(0, float(running_dollars), color="#2563eb", width=0.7)
        for index, change in enumerate(spec.series[0].values_dollars, start=1):
            next_dollars = running_dollars + change
            axes.plot([index - 1 + 0.35, index - 0.35],
                      [float(running_dollars)] * 2, color="#64748b", linewidth=1)
            axes.bar(index, float(abs(change)),
                     bottom=float(min(running_dollars, next_dollars)),
                     color="#ea580c" if change >= 0 else "#0f766e", width=0.7)
            axes.annotate(f"{change:+,.2f}",
                          (index, float(max(running_dollars, next_dollars))),
                          xytext=(0, 5), textcoords="offset points", ha="center", fontsize=9)
            running_dollars = next_dollars
        end_position = len(spec.labels) + 1
        axes.plot([end_position - 1 + 0.35, end_position - 0.35],
                  [float(running_dollars)] * 2, color="#64748b", linewidth=1)
        axes.bar(end_position, float(running_dollars), color="#2563eb", width=0.7)
        axes.margins(y=0.15)
        axes.legend(handles=[
            Patch(facecolor=color, label=label)
            for color, label in (("#2563eb", "Total"), ("#ea580c", "Increase"),
                                 ("#0f766e", "Decrease"))
        ])
