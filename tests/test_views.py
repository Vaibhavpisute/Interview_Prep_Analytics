from __future__ import annotations

import pandas as pd
import pytest

from conftest import make_row, set_scores
from ipa.db import VIEW_NAMES, create_views, init_db
from ipa.ingest import ingest_directory


@pytest.fixture
def small(engine, tmp_path, write_json):
    """Four answers: week of 6 July (SQL 8, SQL 6, Spark 4) and week of 13 July (SQL 10)."""
    rows = [
        make_row(1, session_date="2026-07-06", category="SQL", question="Question text one here?",
                 answer="one two three four five six seven eight nine ten", duration_sec=5),
        make_row(2, session_date="2026-07-08", category="SQL", question="Question text one here?",
                 answer="um uh so basically like this", duration_sec=60),
        make_row(3, session_date="2026-07-09", category="Spark", question="Question text two?"),
        make_row(4, session_date="2026-07-15", category="SQL", question="Question text three?"),
    ]  # fmt: skip
    write_json("a.json", rows)
    ingest_directory(engine, tmp_path / "raw", tmp_path / "rej")
    set_scores(engine, {1: 8, 2: 6, 3: 4, 4: 10})
    return engine


def q(engine, sql):
    return pd.read_sql(sql, engine)


def test_all_views_exist_and_can_be_recreated(engine):
    for name in VIEW_NAMES:
        rows = q(engine, f"SELECT * FROM {name}").shape[0]
        assert rows == (1 if name == "v_repeat_rate_summary" else 0)  # aggregate-only view
    create_views(engine)  # idempotent
    init_db(engine)


def test_score_trend_by_category_per_week(small):
    df = q(small, "SELECT * FROM v_score_trend_by_category ORDER BY week_start, category")
    assert df[["week_start", "category", "answers"]].astype(str).values.tolist() == [
        ["2026-07-06", "SQL", "2"],
        ["2026-07-06", "Spark", "1"],
        ["2026-07-13", "SQL", "1"],
    ]
    assert df["avg_score"].tolist() == [7.0, 4.0, 10.0]
    assert df["min_score"].tolist() == [6, 4, 10]


def test_weakest_categories_are_sorted_lowest_first(small):
    df = q(small, "SELECT * FROM v_weakest_categories")
    assert df["category"].tolist() == ["Spark", "SQL"]
    assert df["avg_score"].round(2).tolist() == [4.0, 8.0]
    assert df["low_scores"].tolist() == [1, 0]  # score <= 5


def test_unscored_answers_are_left_out_of_score_views(small):
    set_scores(small, {1: None})
    df = q(small, "SELECT * FROM v_weakest_categories WHERE category = 'SQL'")
    assert df["answers"].iloc[0] == 2


def test_weekly_pace_and_filler_rate(small):
    df = q(small, "SELECT * FROM v_weekly_delivery ORDER BY week_start")
    week1 = df.iloc[0]
    # answer 1: 10 words in 5 s = 120 wpm; answer 2: 6 words in 60 s = 6 wpm; answer 3 uses make_row
    assert week1["answers"] == 3
    assert df["answers"].tolist() == [3, 1]
    assert df["fillers_per_100_words"].iloc[0] > 0
    assert df["avg_wpm"].iloc[0] == pytest.approx((120 + 6 + week_wpm(small, 3)) / 3)


def week_wpm(engine, answer_id):
    return q(engine, f"SELECT wpm FROM fact_answers WHERE answer_id = {answer_id}")["wpm"].iloc[0]


def test_question_repeat_rate(small):
    repeat = q(small, "SELECT * FROM v_question_repeat ORDER BY question_id")
    assert repeat["times_asked"].tolist() == [2, 1, 1]
    assert repeat["sessions"].tolist() == [2, 1, 1]
    summary = q(small, "SELECT * FROM v_repeat_rate_summary").iloc[0]
    assert (summary["distinct_questions"], summary["repeated_questions"]) == (3, 1)
    assert summary["repeat_rate_pct"] == pytest.approx(33.333, abs=0.01)


def test_views_on_empty_database_return_no_rows_or_nulls(engine):
    summary = q(engine, "SELECT * FROM v_repeat_rate_summary").iloc[0]
    assert summary["distinct_questions"] == 0 and pd.isna(summary["repeat_rate_pct"])
