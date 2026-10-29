"""Export warehouse tables to CSV (used by the R analysis when no database is available)."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

EXPORT_TABLES = [
    "fact_answers",
    "dim_session",
    "dim_question",
    "dim_category",
    "dim_date",
    "anomalies",
]


def export_csv(engine: Engine, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for table in EXPORT_TABLES:
        df = pd.read_sql(f"SELECT * FROM {table}", engine)
        path = out_dir / f"{table}.csv"
        df.to_csv(path, index=False)
        paths.append(path)
        log.info("Exported %s (%d rows)", path, len(df))
    return paths
