from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from Ingest.categories import DEFAULT_CATEGORIES


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OPENAI_DECISIONS_URL = "https://api.openai.com/v1/decisions"
DEFAULT_DECISION_MODEL = "gpt-6-luna"


@dataclass(frozen=True)
class CategoryDecision:
    category: str
    confidence: float


def load_env_file(env_path: Path = PROJECT_ROOT / ".env") -> None:
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def build_transaction_input(row: Mapping[str, str]) -> str:
    return "\n".join(
        (
            "Classify this credit-card transaction into one category.",
            f"Transaction date: {row.get('Transaction Date', '')}",
            f"Post date: {row.get('Post Date', '')}",
            f"Statement type: {row.get('Type', '')}",
            f"Description: {row.get('Description', '')}",
            f"Amount: {row.get('Amount', '')}",
            f"Credit: {row.get('Credit', '')}",
            f"Charge: {row.get('Charge', '')}",
        )
    )


def build_category_choices() -> list[dict[str, str]]:
    return [
        {
            "value": category_name,
            "description": f"{category_name}; transaction group: {category_type}",
        }
        for _, category_name, category_type in DEFAULT_CATEGORIES
    ]


def highest_probability(answer: Mapping[str, object]) -> CategoryDecision:
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, list) or not probabilities:
        choice = str(answer["choice"])
        confidence = float(answer.get("confidence", 0))
        return CategoryDecision(category=choice, confidence=confidence)

    best = max(probabilities, key=lambda item: float(item["probability"]))
    return CategoryDecision(
        category=str(best["value"]),
        confidence=float(best["probability"]),
    )


def categorize_transaction(row: Mapping[str, str]) -> CategoryDecision:
    load_env_file()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required in the environment or .env file")

    payload = {
        "model": os.environ.get("OPENAI_DECISION_MODEL", DEFAULT_DECISION_MODEL),
        "input": build_transaction_input(row),
        "questions": [
            {
                "type": "choice",
                "name": "transaction_category",
                "instructions": (
                    "Choose the single best category for this transaction. "
                    "Use the merchant description, amount sign, and statement type. "
                    "Prefer payment/transfer categories for credit-card payments."
                ),
                "choices": build_category_choices(),
            }
        ],
    }

    request = Request(
        OPENAI_DECISIONS_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8")
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI Decisions API error: {error_body}") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach OpenAI Decisions API: {error}") from error

    decision = json.loads(response_body)
    answer = decision["answers"][0]
    if answer.get("type") == "refusal":
        raise RuntimeError("OpenAI Decisions API refused to categorize transaction")

    result = highest_probability(answer)
    print(f"Decision output: category={result.category}, confidence={result.confidence}")
    return result
