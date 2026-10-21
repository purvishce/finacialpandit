#!/usr/bin/env python
"""Edit assignment below, then run `crewai run` from the project directory."""

import json
import sys

from crewai.flow import Flow, start
from pydantic import BaseModel

from financial_pandit_agents.crews.content_crew.content_crew import ContentCrew, tracing_enabled


# Write the question you want to ask the agents here.
assignment = "Show my spending by category for September 2026 in dollars with meantinful charts and finacial advisor insights."   


class FinancialState(BaseModel):
    user_question: str = ""
    session_context: str = ""
    answer: str = ""


class FinancialFlow(Flow[FinancialState]):
    @start()
    def answer_question(self) -> str:
        if not self.state.user_question.strip():
            raise ValueError("Assignment must contain a financial question.")
        result = ContentCrew().crew().kickoff(inputs={
            "user_question": self.state.user_question,
            "session_context": self.state.session_context,
        })
        self.state.answer = result.raw
        return self.state.answer


def run_assignment(question: str, session_context: str = "") -> str:
    """Run one question with explicitly supplied context and no saved memory."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Assignment must contain a financial question.")
    flow = FinancialFlow(tracing=tracing_enabled())
    flow.kickoff(inputs={"user_question": question, "session_context": session_context})
    return flow.state.answer


def kickoff() -> None:
    print(run_assignment(assignment))


def plot() -> None:
    FinancialFlow().plot("financial_flow")


def run_with_trigger() -> None:
    """Accept a JSON object with assignment and optional session_context."""
    if len(sys.argv) < 2:
        raise ValueError("Provide a JSON payload containing assignment.")
    payload = json.loads(sys.argv[1])
    if not isinstance(payload, dict):
        raise ValueError("Trigger payload must be a JSON object.")
    question = payload.get("assignment", payload.get("user_question", ""))
    context = payload.get("session_context", "")
    if not isinstance(context, str):
        raise ValueError("session_context must be a string.")
    print(run_assignment(question, context))


if __name__ == "__main__":
    kickoff()
