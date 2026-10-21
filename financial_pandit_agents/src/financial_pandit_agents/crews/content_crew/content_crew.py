"""One coordinator-led financial crew with three tool-backed specialists."""

import os
from functools import cached_property
from pathlib import Path

from crewai import Agent, Crew, LLM, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task
from dotenv import load_dotenv

from financial_pandit_agents.tools.spending_chart_tool import SpendingChartTool
from financial_pandit_agents.tools.sqlite_query_tool import SQLiteQueryTool, SQLiteSchemaTool


def load_environment() -> None:
    project_root = Path(__file__).resolve().parents[4]
    load_dotenv(project_root / ".env", override=False)
    load_dotenv(project_root.parent / ".env", override=False)


def tracing_enabled() -> bool:
    """Enable tracing by default; allow an explicit environment override."""
    load_environment()
    return os.getenv("CREWAI_TRACING_ENABLED", "true").strip().lower() in ("true", "1", "yes")


@CrewBase
class ContentCrew:
    """Answer financial questions through coordinator-directed delegation."""

    agents: list[BaseAgent]
    tasks: list[Task]
    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @cached_property
    def shared_llm(self):
        load_environment()
        model = os.getenv("OPENAI_MODEL_NAME") or os.getenv("MODEL") or "openai/gpt-4o"
        if "/" not in model:
            model = f"openai/{model}"
        return LLM(model=model, stream=False)

    @agent
    def coordinator(self) -> Agent:
        return Agent(
            config=self.agents_config["coordinator"],  # type: ignore[index]
            llm=self.shared_llm,
        )

    @agent
    def transaction_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["transaction_analyst"],  # type: ignore[index]
            llm=self.shared_llm,
            tools=[SQLiteSchemaTool(), SQLiteQueryTool()],
        )

    @agent
    def financial_insights_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["financial_insights_analyst"],  # type: ignore[index]
            llm=self.shared_llm,
            tools=[SpendingChartTool()],
        )

    @agent
    def financial_advisor(self) -> Agent:
        return Agent(
            config=self.agents_config["financial_advisor"],  # type: ignore[index]
            llm=self.shared_llm,
        )

    @task
    def coordinator_task(self) -> Task:
        return Task(
            config=self.tasks_config["coordinator_task"],  # type: ignore[index]
        )

    @crew
    def crew(self) -> Crew:
        # Only the coordinator has a scheduled task. Its delegation tools invoke
        # specialists dynamically; they are not run as a fixed task sequence.
        return Crew(
            agents=self.agents,
            tasks=[self.coordinator_task()],
            process=Process.sequential,
            memory=False,
            cache=False,
            verbose=False,
            tracing=tracing_enabled(),
        )
