from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import select, update

from conftest import make_row, set_scores
from ipa.checks import (
    detect_anomalies,
    flagged_sessions,
    load_answers,
    normalise_answer,
    run_checks,
    score_drop_flags,
)
from ipa.db import anomalies, fact_answers
from ipa.ingest import ingest_directory
from ipa.llm.client import LLMClient
from ipa.llm.fake import FakeProvider
from ipa.workflow import score_pending


def frame(scores, category="SQL", start_id=1) -> pd.DataFrame:
    n = len(scores)
    return pd.DataFrame(
        {
            "answer_id": range(start_id, start_id + n),
            "session_id": range(start_id, start_id + n),
            "session_key": [f"S{i}" for i in range(start_id, start_id + n)],
            "category": category,
            "session_date": pd.date_range("2026-07-01", periods=n, freq="D"),
            "score": scores,
            "wpm": 130,
            "answer_text": [f"answer number {i}" for i in range(start_id, start_id + n)],
            "llm_valid": 1,
            "llm_attempts": 1,
        }
    )


# ---------- (a) score drop vs rolling average ----------


def test_score_drop_flags_a_big_fall_after_enough_history():
    df = frame([8, 9, 8, 9, 8, 9, 8, 2])
    flags = score_drop_flags(df)
    assert flags["flagged"].tolist() == [False] * 7 + [True]
    assert flags["z"].iloc[-1] < -2


def test_score_drop_needs_minimum_history():
    df = frame([8, 9, 8, 9, 1])  # only 4 earlier answers
    assert not score_drop_flags(df)["flagged"].any()


def test_score_drop_ignores_zero_variance_and_small_dips():
    assert not score_drop_flags(frame([8] * 6 + [1]))["flagged"].any()  # sd is 0, no z-score
    assert not score_drop_flags(frame([5, 9, 5, 9, 5, 9, 5, 4]))["flagged"].any()


def test_score_drop_is_per_category_and_uses_only_earlier_answers():
    df = pd.concat([frame([8, 9, 8, 9, 8, 9, 8, 2]), frame([2, 3, 2, 3, 2, 3], "Spark", 100)])
    flags = score_drop_flags(df)
    assert set(flags.loc[flags["flagged"], "category"]) == {"SQL"}
    # a later low score must not influence the z of an earlier answer
    assert flags.loc[flags["answer_id"] == 6, "roll_mean"].iloc[0] == pytest.approx(8.4)


def test_score_drop_skips_unscored_answers_and_handles_empty():
    df = frame([8, 9, None, 8, 9, 8, 9, 8, 2])
    flags = score_drop_flags(df)
    assert flags["score"].notna().all() and flags["flagged"].sum() == 1
    assert score_drop_flags(frame([None, None]))["flagged"].sum() == 0


# ---------- (b), (c), (d) ----------


def rules(found):
    return sorted(a["rule"] for a in found)


def test_llm_failures_and_retries_are_flagged():
    df = frame([5, 5, 5])
    df.loc[0, "llm_valid"] = 0
    df.loc[1, "llm_attempts"] = 3
    found = detect_anomalies(df)
    assert rules(found) == ["llm_retry", "llm_validation_failed"]
    retry = next(a for a in found if a["rule"] == "llm_retry")
    assert retry["metric_value"] == 3.0 and retry["severity"] == "low"


def test_pace_outliers_use_90_and_190_limits():
    df = frame([5, 5, 5, 5, 5])
    df["wpm"] = [89, 90, 190, 191, 130]
    found = [a for a in detect_anomalies(df) if a["rule"] == "pace_outlier"]
    assert [a["answer_id"] for a in found] == [1, 4]
    assert "too slow" in found[0]["detail"] and "too fast" in found[1]["detail"]


def test_duplicate_answers_flag_every_later_copy():
    df = frame([5, 5, 5, 5])
    df.loc[1, "answer_text"] = "Same  answer, here!"
    df.loc[2, "answer_text"] = "same answer here"
    df.loc[3, "answer_text"] = "SAME ANSWER HERE."
    found = [a for a in detect_anomalies(df) if a["rule"] == "duplicate_answer"]
    assert [a["answer_id"] for a in found] == [3, 4]
    assert "answer 2" in found[0]["detail"]


def test_normalise_answer():
    assert normalise_answer("  Hello,   WORLD! ") == "hello world"


# ---------- database round trip ----------


def test_run_checks_writes_and_replaces_the_anomalies_table(engine, tmp_path, write_json):
    rows = [make_row(i, session_date=f"2026-07-{i:02d}", question=f"Question text number {i}?")
            for i in range(1, 10)]  # fmt: skip
    write_json("a.json", rows)
    ingest_directory(engine, tmp_path / "raw", tmp_path / "rej")
    set_scores(engine, {1: 8, 2: 9, 3: 8, 4: 9, 5: 8, 6: 9, 7: 8, 8: 1, 9: 8})
    with engine.begin() as conn:
        conn.execute(update(fact_answers).where(fact_answers.c.answer_id == 9).values(wpm=250))
    summary = run_checks(engine)
    assert summary.by_rule == {"score_drop": 1, "pace_outlier": 1}
    first = len(_all(engine))
    assert run_checks(engine).total == summary.total  # rewritten, not appended
    assert len(_all(engine)) == first
    assert flagged_sessions(engine) == {"T008"}


def _all(engine):
    with engine.connect() as conn:
        return conn.execute(select(anomalies)).all()


def test_checks_on_committed_sample_data_find_every_rule(loaded_engine):
    score_pending(loaded_engine, LLMClient(FakeProvider(), sleep=lambda s: None, max_retries=3))
    summary = run_checks(loaded_engine)
    assert set(summary.by_rule) == {
        "score_drop", "llm_validation_failed", "llm_retry", "pace_outlier", "duplicate_answer",
    }  # fmt: skip
    df = load_answers(loaded_engine)
    assert len(df) == 96
