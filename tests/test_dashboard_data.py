from __future__ import annotations

from datetime import date

import pytest

from ipa.checks import run_checks
from ipa.dashboard.data import (
    apply_filters,
    default_database_url,
    headline_metrics,
    load_frames,
    score_trend,
    weak_areas,
    weekly_delivery,
)
from ipa.llm.client import LLMClient
from ipa.llm.fake import FakeProvider
from ipa.workflow import score_pending


@pytest.fixture
def frames(loaded_engine):
    score_pending(loaded_engine, LLMClient(FakeProvider(), sleep=lambda s: None, max_retries=3))
    run_checks(loaded_engine)
    return load_frames(loaded_engine)


def test_trend_and_weak_areas(frames):
    trend = score_trend(frames["answers"])
    assert list(trend.columns) == sorted(trend.columns) and len(trend) == 10
    weak = weak_areas(frames["answers"])
    assert len(weak) == 6 and weak["avg_score"].is_monotonic_increasing


def test_filters_by_category_date_and_rule(frames):
    view = apply_filters(frames, ["SQL"], date(2026, 7, 6), date(2026, 7, 31), ["pace_outlier"])
    assert set(view["answers"]["category"]) == {"SQL"}
    assert view["answers"]["session_date"].max() <= date(2026, 7, 31)
    assert set(view["anomalies"]["rule"]) <= {"pace_outlier"}
    everything = apply_filters(frames)
    assert len(everything["answers"]) == 96 and len(everything["anomalies"]) == 16


def test_weekly_delivery_and_headline_metrics(frames):
    delivery = weekly_delivery(frames["answers"])
    assert len(delivery) == 10 and (delivery["avg_wpm"] > 0).all()
    m = headline_metrics(frames["answers"], frames["anomalies"])
    assert (m["sessions"], m["answers"], m["anomalies"]) == (30, 96, 16)
    assert weekly_delivery(frames["answers"].iloc[0:0]).empty
    assert score_trend(frames["answers"].assign(score=None)).empty
    empty = headline_metrics(frames["answers"].iloc[0:0], frames["anomalies"].iloc[0:0])
    assert empty["avg_score"] is None and empty["avg_wpm"] is None


def test_default_database_url(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert default_database_url() == "sqlite:///data/demo.db"
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "ipa.db").write_text("")
    assert default_database_url() == "sqlite:///data/ipa.db"
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    assert default_database_url() == "postgresql://x"
