"""Data preparation for the dashboard (kept separate from Streamlit so it can be tested)."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy.engine import Engine

from ipa.report import ANOMALY_SQL

ANSWERS_SQL = """
SELECT f.answer_id, s.session_key, c.name AS category, d.full_date AS session_date,
       d.week_start AS week_start, f.score, f.wpm, f.word_count, f.filler_count
FROM fact_answers f
JOIN dim_session s  ON s.session_id = f.session_id
JOIN dim_category c ON c.category_id = f.category_id
JOIN dim_date d     ON d.date_id = f.date_id
ORDER BY d.full_date, f.answer_id
"""


def default_database_url() -> str:
    """DATABASE_URL if set, else the normal SQLite file, else the demo database."""
    env = os.environ.get("DATABASE_URL")
    if env:
        return env
    for candidate in ("data/ipa.db", "data/demo.db"):
        if Path(candidate).exists():
            return f"sqlite:///{candidate}"
    return "sqlite:///data/demo.db"


def load_frames(engine: Engine) -> dict[str, pd.DataFrame]:
    answers = pd.read_sql(ANSWERS_SQL, engine)
    answers["session_date"] = pd.to_datetime(answers["session_date"]).dt.date
    answers["week_start"] = pd.to_datetime(answers["week_start"]).dt.date
    anomalies = pd.read_sql(ANOMALY_SQL, engine)
    return {"answers": answers, "anomalies": anomalies}


def apply_filters(
    frames: dict[str, pd.DataFrame],
    categories: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    rules: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    answers, anomalies = frames["answers"], frames["anomalies"]
    if categories:
        answers = answers[answers["category"].isin(categories)]
        anomalies = anomalies[anomalies["category"].isin(categories) | anomalies["category"].isna()]
    if start:
        answers = answers[answers["session_date"] >= start]
    if end:
        answers = answers[answers["session_date"] <= end]
    if rules:
        anomalies = anomalies[anomalies["rule"].isin(rules)]
    return {"answers": answers, "anomalies": anomalies}


def score_trend(answers: pd.DataFrame) -> pd.DataFrame:
    """Average score per week (rows) and category (columns)."""
    scored = answers.dropna(subset=["score"])
    if scored.empty:
        return pd.DataFrame()
    return scored.pivot_table(
        index="week_start", columns="category", values="score", aggfunc="mean"
    ).round(2)


def weak_areas(answers: pd.DataFrame) -> pd.DataFrame:
    scored = answers.dropna(subset=["score"])
    out = (
        scored.groupby("category")["score"]
        .agg(avg_score="mean", answers="count")
        .round(2)
        .sort_values("avg_score")
        .reset_index()
    )
    return out


def weekly_delivery(answers: pd.DataFrame) -> pd.DataFrame:
    if answers.empty:
        return pd.DataFrame()
    g = answers.groupby("week_start")
    out = pd.DataFrame(
        {
            "avg_wpm": g["wpm"].mean().round(1),
            "fillers_per_100_words": (100 * g["filler_count"].sum() / g["word_count"].sum()).round(
                2
            ),
        }
    )
    return out


def headline_metrics(answers: pd.DataFrame, anomalies: pd.DataFrame) -> dict[str, object]:
    scored = answers.dropna(subset=["score"])
    return {
        "answers": len(answers),
        "sessions": answers["session_key"].nunique(),
        "avg_score": round(float(scored["score"].mean()), 2) if len(scored) else None,
        "avg_wpm": round(float(answers["wpm"].mean()), 0) if len(answers) else None,
        "anomalies": len(anomalies),
    }
