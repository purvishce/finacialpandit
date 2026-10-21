"""Verify crew wiring and assignment inputs without calling a model."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from crewai import BaseLLM

from financial_pandit_agents import main
from financial_pandit_agents.crews.content_crew.content_crew import ContentCrew


class OfflineLLM(BaseLLM):
    def call(self, *args, **kwargs):
        raise AssertionError("Tests must not make model calls.")


class FinancialFlowTests(unittest.TestCase):
    def setUp(self):
        # CrewAI allocates local replay storage even with crew memory disabled.
        storage = Path(__file__).resolve().parents[2] / ".cache" / "crewai-tests"
        environment = patch.dict("os.environ", {
            "CREWAI_STORAGE_DIR": str(storage), "CREWAI_TRACING_ENABLED": "false",
        })
        environment.start()
        self.addCleanup(environment.stop)

    def test_one_coordinator_task_four_agents_and_correct_tool_permissions(self):
        llm = OfflineLLM(model="offline-test")
        with patch("financial_pandit_agents.crews.content_crew.content_crew.LLM", return_value=llm):
            crew = ContentCrew().crew()
        self.assertEqual(len(crew.agents), 4)
        self.assertEqual(len(crew.tasks), 1)
        by_role = {agent.role.strip(): agent for agent in crew.agents}
        coordinator = by_role["Financial Analysis Coordinator"]
        self.assertIs(crew.tasks[0].agent, coordinator)
        self.assertTrue(coordinator.allow_delegation)
        for role, agent in by_role.items():
            self.assertIs(agent.llm, llm)
            if agent is not coordinator:
                self.assertFalse(agent.allow_delegation)
        self.assertEqual(
            {tool.name for tool in by_role["Transaction Analyst"].tools},
            {"inspect_transaction_schema", "query_transactions"},
        )
        self.assertEqual(
            {tool.name for tool in by_role["Financial Insights Analyst"].tools},
            {"create_spending_chart"},
        )
        self.assertEqual(by_role["Financial Advisor"].tools, [])
        self.assertEqual(len(coordinator.get_delegation_tools(
            [agent for agent in crew.agents if agent is not coordinator]
        )), 2)
        self.assertFalse(crew.memory)

    def test_flow_passes_assignment_and_context_and_returns_answer(self):
        with patch.object(main, "ContentCrew") as factory:
            kickoff = factory.return_value.crew.return_value.kickoff
            kickoff.return_value = SimpleNamespace(raw="Verified spending report")
            answer = main.run_assignment("Compare it with August", "Earlier question: September 2026")
        self.assertEqual(answer, "Verified spending report")
        kickoff.assert_called_once_with(inputs={
            "user_question": "Compare it with August",
            "session_context": "Earlier question: September 2026",
        })

    def test_entry_point_uses_editable_assignment(self):
        with patch.object(main, "run_assignment", return_value="Answer") as run:
            with patch("builtins.print") as print_answer:
                self.assertIsNone(main.kickoff())
        run.assert_called_once_with(main.assignment)
        print_answer.assert_called_once_with("Answer")

    def test_blank_assignments_fail_before_building_a_crew(self):
        with patch.object(main, "ContentCrew") as factory:
            for question in ("", "  ", None):
                with self.subTest(question=question), self.assertRaises(ValueError):
                    main.run_assignment(question)
            factory.assert_not_called()

    def test_trigger_passes_question_and_context(self):
        with patch.object(main.sys, "argv", ["trigger", '{"assignment":"Show spending", "session_context":"September 2026"}']):
            with patch.object(main, "run_assignment", return_value="Answer") as run:
                with patch("builtins.print"):
                    main.run_with_trigger()
        run.assert_called_once_with("Show spending", "September 2026")


if __name__ == "__main__":
    unittest.main()
