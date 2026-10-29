"""Prompt builders and loose JSON parsing.

Re-written in Python from the earlier prototype (mockQuestionsPrompt, mockEvaluatePrompt,
parseJsonLoose). Prompts are plain functions so they are easy to unit test.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ipa.config import CATEGORIES
from ipa.speech import SpeechMetrics

MAX_JD_CHARS = 8000

# Markers the fake LLM (and tests) use to recognise the task and the user's answer.
TASK_QUESTIONS = "TASK: generate_questions"
TASK_SCORE = "TASK: score_answer"
ANSWER_START = "ANSWER_START"
ANSWER_END = "ANSWER_END"
REPAIR_MARKER = "YOUR PREVIOUS REPLY WAS INVALID"

SYSTEM_PROMPT = (
    "You are a supportive but honest interview coach for data engineering candidates. "
    "This is for practice only. Reply with a single JSON object and nothing else."
)


def clip(text: str, limit: int) -> str:
    t = text.strip()
    return t if len(t) <= limit else t[:limit] + "\n[...truncated]"


def questions_prompt(jd_text: str, n: int) -> str:
    return "\n".join(
        [
            TASK_QUESTIONS,
            f"COUNT: {n}",
            f"Act as an experienced interviewer. Write {n} practice interview questions for the "
            "job below, spread across these categories: " + ", ".join(CATEGORIES) + ".",
            "Tailor them to the job description. Technical questions must be answerable verbally.",
            'Return JSON only, in this shape: {"questions":[{"question":"...",'
            '"category":"one of the categories above","what_good_looks_like":"one sentence"}]}',
            "",
            "## Job description",
            clip(jd_text, MAX_JD_CHARS),
        ]
    )


def score_prompt(question: str, category: str, answer: str, metrics: SpeechMetrics) -> str:
    return "\n".join(
        [
            TASK_SCORE,
            "Evaluate the candidate's practice answer (it may be a speech-to-text transcript, "
            "so ignore small transcription errors).",
            f"Question ({category}): {question}",
            f"{ANSWER_START}\n{answer.strip() or '(no answer given)'}\n{ANSWER_END}",
            f"Delivery: {metrics.words} words in {metrics.duration_sec}s "
            f"({metrics.words_per_minute} words per minute), {metrics.filler_count} filler words.",
            "Score from 1 to 10 on content, structure, relevance and clarity.",
            'Return JSON only: {"score": 7, "strengths": ["..."], "improvements": ["..."], '
            '"improved_answer": "a stronger first-person version using only facts in the answer"}',
        ]
    )


def repair_prompt(original: str, error: str) -> str:
    return (
        f"{original}\n\n{REPAIR_MARKER}: {error[:300]}\n"
        "Reply again with ONLY one valid JSON object in the requested shape."
    )


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def parse_json_loose(text: str) -> Any:
    """Extract the first JSON object from model output that may contain prose or code fences.

    Small, safe repairs are applied: code fences, text around the object, trailing commas.
    Raises ValueError when no valid JSON object can be recovered.
    """
    fenced = _FENCE.search(text)
    body = fenced.group(1) if fenced else text
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("the model did not return a JSON object")
    candidate = body[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    repaired = _TRAILING_COMMA.sub(r"\1", candidate)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON from the model: {exc.msg}") from exc
