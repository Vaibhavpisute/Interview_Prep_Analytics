"""Proves that the Python and R implementations flag the same sessions.

Runs only when Rscript and the needed R packages are installed (the CI "R" job always has them).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from ipa.checks import flagged_sessions, run_checks
from ipa.export import export_csv
from ipa.llm.client import LLMClient
from ipa.llm.fake import FakeProvider
from ipa.workflow import score_pending

REPO = Path(__file__).resolve().parent.parent
RSCRIPT = shutil.which("Rscript")


def _r_ready() -> bool:
    if not RSCRIPT:
        return False
    probe = subprocess.run(
        [RSCRIPT, "-e", "for (p in c('dplyr','readr','ggplot2')) library(p, character.only=TRUE)"],
        capture_output=True,
    )
    return probe.returncode == 0


@pytest.mark.skipif(not _r_ready(), reason="Rscript or R packages not available")
def test_python_and_r_flag_the_same_sessions(loaded_engine, tmp_path):
    score_pending(loaded_engine, LLMClient(FakeProvider(), sleep=lambda s: None, max_retries=3))
    run_checks(loaded_engine)
    export_csv(loaded_engine, tmp_path / "export")
    out_csv = tmp_path / "r_anomalies.csv"
    env = {
        **os.environ,
        "IPA_EXPORT_DIR": str(tmp_path / "export"),
        "IPA_R_OUT": str(out_csv),
        "IPA_R_CHARTS": str(tmp_path / "charts"),
    }
    env.pop("IPA_PG_HOST", None)
    run = subprocess.run(
        [RSCRIPT, str(REPO / "r" / "anomaly_stats.R")],
        cwd=REPO, env=env, capture_output=True, text=True,
    )  # fmt: skip
    assert run.returncode == 0, run.stderr
    r_sessions = set(pd.read_csv(out_csv)["session_key"])
    assert r_sessions == flagged_sessions(loaded_engine) != set()
    assert (tmp_path / "charts" / "score_trend_by_category.png").exists()
    assert (tmp_path / "charts" / "pace_distribution.png").exists()
