"""Pydantic models: raw input rows and the strict JSON the LLM must return."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

from ipa.config import CATEGORIES

_ALIASES = {
    "behavioral": "Behavioural",
    "behavioural": "Behavioural",
    "data modeling": "Data Modelling",
    "data modelling": "Data Modelling",
    "sql": "SQL",
    "python": "Python",
    "spark": "Spark",
    "etl": "ETL",
}

Text = Annotated[str, StringConstraints(strip_whitespace=True)]


def normalise_category(value: str) -> str:
    """Map a free-text category to one of the six known names, or raise ValueError."""
    key = " ".join(str(value).lower().split())
    if key not in _ALIASES:
        raise ValueError(f"unknown category '{value}', expected one of {CATEGORIES}")
    return _ALIASES[key]


class RawRecord(BaseModel):
    """One practice answer as found in data/raw/*.json or *.csv."""

    session_id: Text = Field(min_length=1, max_length=128)
    session_date: date
    job_title: Text = Field(min_length=2, max_length=128)
    category: str
    question: Text = Field(min_length=10)
    answer: Text = Field(min_length=3)
    duration_sec: float = Field(gt=0, le=1800)

    @field_validator("category")
    @classmethod
    def _check_category(cls, v: str) -> str:
        return normalise_category(v)


class ScoreResult(BaseModel):
    """Strict shape of the LLM scoring answer."""

    score: int = Field(ge=1, le=10)
    strengths: list[str] = Field(min_length=1)
    improvements: list[str] = Field(min_length=1)
    improved_answer: Text = Field(min_length=1)

    @field_validator("strengths", "improvements")
    @classmethod
    def _non_empty_items(cls, v: list[str]) -> list[str]:
        cleaned = [s.strip() for s in v if isinstance(s, str) and s.strip()]
        if not cleaned:
            raise ValueError("list must contain at least one non-empty string")
        return cleaned


class GeneratedQuestion(BaseModel):
    question: Text = Field(min_length=10)
    category: str
    what_good_looks_like: Text = Field(min_length=1)

    @field_validator("category")
    @classmethod
    def _check_category(cls, v: str) -> str:
        return normalise_category(v)


class QuestionList(BaseModel):
    questions: list[GeneratedQuestion] = Field(min_length=1)
