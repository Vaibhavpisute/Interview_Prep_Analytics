"""A deterministic, offline stand-in for an LLM. Used by tests and by `ipa demo`.

It never touches the network. Scores come from simple text features (length, numbers, an
example, reasoning words, filler words), so the same answer always gets the same score.
To exercise the retry logic, some prompts get an invalid reply on the first try, and a very
small share never get a valid reply.
"""

from __future__ import annotations

import hashlib
import json
import re

from ipa.config import CATEGORIES
from ipa.llm.prompts import (
    ANSWER_END,
    ANSWER_START,
    REPAIR_MARKER,
    TASK_QUESTIONS,
    TASK_SCORE,
)
from ipa.speech import count_fillers

_QUESTION_BANK: dict[str, list[tuple[str, str]]] = {
    "SQL": [
        (
            "How would you tune a slow query that joins three large tables?",
            "Mentions execution plans, indexes and statistics.",
        ),
        (
            "When would you use a window function instead of a GROUP BY?",
            "Explains ranking and running totals with an example.",
        ),
    ],
    "Python": [
        (
            "How do you structure and test a Python ETL job?",
            "Covers pure functions, pytest and logging.",
        ),
        (
            "How do you process a file that does not fit in memory?",
            "Mentions chunking, generators or Parquet.",
        ),
    ],
    "Spark": [
        (
            "How do you find and fix a skewed join in Spark?",
            "Mentions the Spark UI, salting or broadcast joins.",
        ),
        (
            "How do you decide how many partitions a Spark job should use?",
            "Talks about data size, cores and small files.",
        ),
    ],
    "ETL": [
        ("How do you make a pipeline safe to re-run?", "Explains idempotency, keys and load logs."),
        (
            "What data quality checks would you add to a new pipeline?",
            "Covers completeness, uniqueness and freshness.",
        ),
    ],
    "Data Modelling": [
        (
            "How would you design a star schema for this team's reporting?",
            "Defines grain, facts and dimensions.",
        ),
        (
            "How do you handle a dimension attribute that changes over time?",
            "Explains slowly changing dimensions.",
        ),
    ],
    "Behavioural": [
        (
            "Tell me about a pipeline failure you fixed under pressure.",
            "Uses a clear situation, action and result.",
        ),
        (
            "How do you explain a technical trade-off to a non-technical stakeholder?",
            "Uses plain language and a concrete example.",
        ),
    ],
}

_ANSWER_RE = re.compile(rf"{ANSWER_START}\n(.*?)\n{ANSWER_END}", re.DOTALL)
_COUNT_RE = re.compile(r"COUNT:\s*(\d+)")


def _digest(prompt: str) -> int:
    return int(hashlib.sha256(prompt.encode("utf-8")).hexdigest(), 16)


class FakeProvider:
    name = "fake"
    default_model = "fake-model-1"
    default_fallback_model = "fake-model-fallback"

    # Share of prompts that get an invalid first reply / never get a valid reply.
    first_try_invalid_every = 13
    always_invalid_every = 23

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system: str, prompt: str, model: str) -> str:
        self.calls += 1
        if TASK_QUESTIONS in prompt:
            return self._questions(prompt)
        if TASK_SCORE in prompt:
            return self._score(prompt)
        return json.dumps({"error": "unknown task"})

    def _questions(self, prompt: str) -> str:
        m = _COUNT_RE.search(prompt)
        n = int(m.group(1)) if m else 5
        lower = prompt.lower()
        # Put categories mentioned in the job description first.
        order = sorted(CATEGORIES, key=lambda c: (c.lower() not in lower, CATEGORIES.index(c)))
        items = []
        for i in range(n):
            cat = order[i % len(order)]
            q, good = _QUESTION_BANK[cat][(i // len(order)) % len(_QUESTION_BANK[cat])]
            items.append({"question": q, "category": cat, "what_good_looks_like": good})
        return json.dumps({"questions": items})

    def _score(self, prompt: str) -> str:
        # Hash the original prompt, so a repair request is judged like the first request.
        h = _digest(prompt.split(REPAIR_MARKER)[0])
        repair = REPAIR_MARKER in prompt
        if h % self.always_invalid_every == 0:
            return "I think this answer is good, around eight out of ten."
        if not repair and h % self.first_try_invalid_every == 0:
            return json.dumps({"score": 11, "strengths": [], "improvements": []})
        m = _ANSWER_RE.search(prompt)
        answer = m.group(1) if m else ""
        return json.dumps(score_answer_text(answer, h))


def score_answer_text(answer: str, h: int = 0) -> dict[str, object]:
    lower = answer.lower()
    words = len(answer.split())
    fillers = sum(count_fillers(answer).values())
    has_number = bool(re.search(r"\d", answer))
    has_example = bool(re.search(r"for example|for instance", lower))
    has_reason = bool(re.search(r"because|so that|trade-off|this matters|that is why", lower))

    raw = 1.5 + min(words / 14, 5)
    raw += 1.0 * has_number + 1.0 * has_example + 0.5 * has_reason
    raw -= min(fillers * 0.3, 1.0)
    raw += (h % 3 - 1) * 0.5
    score = max(1, min(10, int(raw + 0.5)))

    strengths = []
    if words >= 45:
        strengths.append("Good level of detail.")
    if has_example:
        strengths.append("Uses a concrete example.")
    if has_number:
        strengths.append("Backs the answer with numbers.")
    if has_reason:
        strengths.append("Explains the reasoning behind the choice.")
    improvements = []
    if words < 45:
        improvements.append("Add more detail: approach, reason, then a short example.")
    if not has_example:
        improvements.append("Give one concrete example from your own work.")
    if not has_number:
        improvements.append("Add a measurable result (time saved, rows, percent).")
    if fillers >= 2:
        improvements.append(f"Cut filler words (found {fillers}).")
    improved = answer.strip()
    if words < 45:
        improved = (
            "I would start with the direct answer, explain why, and give a short example with "
            "a number. " + improved
        )
    return {
        "score": score,
        "strengths": strengths or ["Clear attempt at answering the question."],
        "improvements": improvements or ["Keep practising to stay consistent."],
        "improved_answer": improved,
    }
