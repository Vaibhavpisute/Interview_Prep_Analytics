"""Streamlit dashboard: score trends, weak areas, delivery and anomalies.

Run:  streamlit run src/ipa/dashboard/app.py
"""

from __future__ import annotations

import altair as alt
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from ipa.dashboard.data import (
    apply_filters,
    default_database_url,
    headline_metrics,
    load_frames,
    score_trend,
    weak_areas,
    weekly_delivery,
)
from ipa.db import make_engine

st.set_page_config(page_title="Interview Prep Analytics", page_icon=":bar_chart:", layout="wide")
st.title("Interview Prep Analytics")
st.caption("Practice sessions only. This dashboard does not help during live interviews.")

url = st.sidebar.text_input("Database URL", default_database_url())
try:
    frames = load_frames(make_engine(url))
except SQLAlchemyError as exc:
    st.error(f"Could not read the database. Run `ipa demo` first. ({type(exc).__name__})")
    st.stop()

answers = frames["answers"]
if answers.empty:
    st.warning("No answers loaded yet. Run `ipa demo` (or `ipa ingest` and `ipa score`).")
    st.stop()

st.sidebar.header("Filters")
all_cats = sorted(answers["category"].unique())
cats = st.sidebar.multiselect("Category", all_cats, default=all_cats)
first, last = answers["session_date"].min(), answers["session_date"].max()
picked = st.sidebar.date_input("Date range", (first, last), min_value=first, max_value=last)
start, end = picked if isinstance(picked, tuple) and len(picked) == 2 else (first, last)
all_rules = sorted(frames["anomalies"]["rule"].unique()) if len(frames["anomalies"]) else []
rules = st.sidebar.multiselect("Anomaly rule", all_rules, default=all_rules)

view = apply_filters(frames, cats, start, end, rules)
m = headline_metrics(view["answers"], view["anomalies"])
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Sessions", m["sessions"])
c2.metric("Answers", m["answers"])
c3.metric("Average score", m["avg_score"] if m["avg_score"] is not None else "n/a")
c4.metric("Average pace (wpm)", m["avg_wpm"] if m["avg_wpm"] is not None else "n/a")
c5.metric("Anomalies", m["anomalies"])

left, right = st.columns(2)
with left:
    st.subheader("Score trend per category")
    trend = score_trend(view["answers"])
    if trend.empty:
        st.info("No scored answers in this selection.")
    else:
        st.line_chart(trend)
with right:
    st.subheader("Weak areas (lowest average score first)")
    weak = weak_areas(view["answers"])
    if weak.empty:
        st.info("No scored answers in this selection.")
    else:
        bars = (
            alt.Chart(weak)
            .mark_bar()
            .encode(
                x=alt.X("category:N", sort="y", title=None),
                y=alt.Y("avg_score:Q", title="Average score", scale=alt.Scale(domain=[0, 10])),
                tooltip=["category", "avg_score", "answers"],
            )
        )
        st.altair_chart(bars, use_container_width=True)
        st.dataframe(weak, hide_index=True, use_container_width=True)

st.subheader("Pace and filler words per week")
delivery = weekly_delivery(view["answers"])
if not delivery.empty:
    a, b = st.columns(2)
    a.line_chart(delivery["avg_wpm"])
    b.line_chart(delivery["fillers_per_100_words"])

st.subheader("Anomalies")
st.dataframe(
    view["anomalies"].drop(columns=["detected_at"], errors="ignore"),
    hide_index=True,
    use_container_width=True,
)
