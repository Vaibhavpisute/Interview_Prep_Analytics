"""Anomaly and data-quality checks. Results are written to the `anomalies` table.

Rules
  score_drop            an answer scores more than 2 standard deviations below the rolling mean
                        of the previous answers in the same category (the session is flagged)
  llm_validation_failed the LLM never returned valid JSON for the answer
  llm_retry             the LLM needed more than one attempt to return valid JSON
  pace_outlier          speaking pace below 90 or above 190 words per minute
  duplicate_answer      the same answer text appears more than once

The score_drop rule is mirrored in R (r/anomaly_stats.R) and a test proves both flag the same
sessions, so keep the two definitions in sync.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import delete, insert
from sqlalchemy.engine import Engine

from ipa.db import anomalies

log = logging.getLogger(__name__)

ROLLING_WINDOW = 10  # previous answers in the same category
MIN_HISTORY = 5  # need at least this many earlier answers before judging
Z_THRESHOLD = -2.0
WPM_MIN = 90
WPM_MAX = 190

ANSWERS_SQL = """
SELECT f.answer_id, f.session_id, s.session_key, c.name AS category, d.full_date AS session_date,
       f.score, f.wpm, f.answer_text, f.llm_valid, f.llm_attempts
FROM fact_answers f
JOIN dim_session s  ON s.session_id = f.session_id
JOIN dim_category c ON c.category_id = f.category_id
JOIN dim_date d     ON d.date_id = f.date_id
ORDER BY d.full_date, f.answer_id
"""


@dataclass
class CheckSummary:
    by_rule: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.by_rule.values())


def load_answers(engine: Engine) -> pd.DataFrame:
    df = pd.read_sql(ANSWERS_SQL, engine)
    df["session_date"] = pd.to_datetime(df["session_date"])
    return df


def score_drop_flags(
    df: pd.DataFrame,
    window: int = ROLLING_WINDOW,
    min_history: int = MIN_HISTORY,
    threshold: float = Z_THRESHOLD,
) -> pd.DataFrame:
    """Return scored answers with rolling mean/sd/z, per category, using only earlier answers.

    Order is (session date, answer_id). Sample standard deviation (n - 1). A zero standard
    deviation gives no z-score (the answer is never flagged).
    """
    scored = df[df["score"].notna()].sort_values(["session_date", "answer_id"])
    parts = []
    for _, g in scored.groupby("category", sort=True):
        g = g.copy()
        prev = g["score"].astype(float).shift(1)
        roll = prev.rolling(window=window, min_periods=min_history)
        g["roll_mean"] = roll.mean()
        g["roll_sd"] = roll.std(ddof=1)
        sd = g["roll_sd"].where(g["roll_sd"] > 0)
        g["z"] = (g["score"] - g["roll_mean"]) / sd
        g["flagged"] = g["z"] < threshold
        parts.append(g)
    if not parts:
        return scored.assign(roll_mean=None, roll_sd=None, z=None, flagged=False)
    return pd.concat(parts).sort_values(["session_date", "answer_id"])


def normalise_answer(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", " ".join(text.lower().split()))


def detect_anomalies(df: pd.DataFrame) -> list[dict[str, object]]:
    """Apply all rules to the answers table and return anomaly rows."""
    found: list[dict[str, object]] = []

    # (a) score drops vs rolling average
    for r in score_drop_flags(df).query("flagged").itertuples():
        found.append(
            {
                "rule": "score_drop",
                "severity": "high" if r.z < -3 else "medium",
                "answer_id": int(r.answer_id),
                "session_id": int(r.session_id),
                "category": r.category,
                "metric_value": round(float(r.z), 2),
                "detail": (
                    f"Score {int(r.score)} is {abs(r.z):.1f} SD below the rolling average "
                    f"{r.roll_mean:.1f} for {r.category} (session {r.session_key})"
                ),
            }
        )

    # (b) LLM outputs that failed validation or needed a retry
    for r in df.itertuples():
        if r.llm_valid == 0:
            found.append(
                {
                    "rule": "llm_validation_failed",
                    "severity": "high",
                    "answer_id": int(r.answer_id),
                    "session_id": int(r.session_id),
                    "category": r.category,
                    "metric_value": None,
                    "detail": f"LLM output was never valid JSON (session {r.session_key})",
                }
            )
        elif pd.notna(r.llm_attempts) and r.llm_attempts > 1:
            found.append(
                {
                    "rule": "llm_retry",
                    "severity": "low",
                    "answer_id": int(r.answer_id),
                    "session_id": int(r.session_id),
                    "category": r.category,
                    "metric_value": float(r.llm_attempts),
                    "detail": f"LLM needed {int(r.llm_attempts)} attempts "
                    f"(session {r.session_key})",
                }
            )

    # (c) speaking pace outliers
    for r in df.itertuples():
        if r.wpm and (r.wpm < WPM_MIN or r.wpm > WPM_MAX):
            kind = "too slow" if r.wpm < WPM_MIN else "too fast"
            found.append(
                {
                    "rule": "pace_outlier",
                    "severity": "medium",
                    "answer_id": int(r.answer_id),
                    "session_id": int(r.session_id),
                    "category": r.category,
                    "metric_value": float(r.wpm),
                    "detail": f"{int(r.wpm)} wpm is {kind} (allowed {WPM_MIN}-{WPM_MAX}), "
                    f"session {r.session_key}",
                }
            )

    # (d) duplicate answers: every later copy of the same text is flagged
    first_seen: dict[str, tuple[int, str]] = {}
    for r in df.sort_values("answer_id").itertuples():
        key = hashlib.sha256(normalise_answer(r.answer_text).encode()).hexdigest()
        if key in first_seen:
            first_id, first_session = first_seen[key]
            found.append(
                {
                    "rule": "duplicate_answer",
                    "severity": "low",
                    "answer_id": int(r.answer_id),
                    "session_id": int(r.session_id),
                    "category": r.category,
                    "metric_value": float(first_id),
                    "detail": f"Same answer text as answer {first_id} (session {first_session})",
                }
            )
        else:
            first_seen[key] = (int(r.answer_id), str(r.session_key))
    return found


def run_checks(engine: Engine) -> CheckSummary:
    """Run every rule and replace the contents of the anomalies table."""
    df = load_answers(engine)
    rows = detect_anomalies(df)
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as conn:
        conn.execute(delete(anomalies))
        if rows:
            conn.execute(insert(anomalies), [{**r, "detected_at": now} for r in rows])
    by_rule: dict[str, int] = {}
    for r in rows:
        by_rule[str(r["rule"])] = by_rule.get(str(r["rule"]), 0) + 1
    for rule, n in sorted(by_rule.items()):
        log.info("Anomaly rule %-22s %d", rule, n)
    return CheckSummary(by_rule)


def flagged_sessions(engine: Engine) -> set[str]:
    """Session keys flagged by the score_drop rule (used by the Python/R parity test)."""
    df = score_drop_flags(load_answers(engine))
    return set(df.loc[df["flagged"], "session_key"])
