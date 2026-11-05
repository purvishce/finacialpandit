#!/usr/bin/env python
"""Edit assignment below, then run the project."""

import json
import sys

from financial_pandit_agents.crews.content_crew.content_crew import ContentCrew


assignment = (
    "Show my spending by category for September 2026 compared to "
    "the prior month in dollars, with meaningful charts "
    "and financial advisor insights."
)


def run_assignment(question: str, session_context: str = "") -> str:
    """Send the question directly to the Crew and return its answer."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Assignment must contain a financial question.")

    if not isinstance(session_context, str):
        raise ValueError("session_context must be a string.")

    result = ContentCrew().crew().kickoff(
        inputs={
            "user_question": question,
            "session_context": session_context,
        }
    )

    return result.raw


def run() -> None:
    """Run the assignment and print the answer."""
    answer = run_assignment(assignment)
    print(answer)


def kickoff() -> None:
    """Keep compatibility with an entry point named kickoff."""
    run()


def run_with_trigger() -> None:
    """Accept a JSON object with assignment and optional session_context."""
    if len(sys.argv) < 2:
        raise ValueError("Provide a JSON payload containing assignment.")

    payload = json.loads(sys.argv[1])

    if not isinstance(payload, dict):
        raise ValueError("Trigger payload must be a JSON object.")

    question = payload.get(
        "assignment",
        payload.get("user_question", ""),
    )
    context = payload.get("session_context", "")

    answer = run_assignment(question, context)
    print(answer)


if __name__ == "__main__":
    run()