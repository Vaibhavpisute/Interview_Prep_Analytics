"""Draw the star schema as docs/erd.png (matplotlib; columns come from ipa.db.metadata)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

from ipa.db import metadata  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "docs" / "erd.png"
LINE_H = 2.1
HEAD_H = 3.2
FACT_SHOWN = [
    "answer_id", "answer_key (unique)", "session_id", "question_id", "category_id", "date_id",
    "answer_text", "duration_sec", "word_count", "wpm", "filler_count", "fillers_json",
    "score", "strengths_json", "improvements_json", "improved_answer",
    "llm_provider / llm_model", "llm_attempts / llm_valid / llm_error", "scored_at",
]  # fmt: skip


PK_FK = {"answer_id": "PK ", "session_id": "FK ", "question_id": "FK ", "category_id": "FK ",
         "date_id": "FK "}  # fmt: skip
FACT_LINES = [f"{PK_FK.get(c, '   ')}{c}" for c in FACT_SHOWN]


def columns(table: str) -> list[str]:
    t = metadata.tables[table]
    fks = {c.name for c in t.columns if c.foreign_keys}
    lines = []
    for c in t.columns:
        tag = "PK " if c.primary_key else ("FK " if c.name in fks else "      ")
        lines.append(f"{tag}{c.name}")
    return lines


def box(ax, x, y, w, name, lines, color, text_color="white"):
    h = HEAD_H + LINE_H * len(lines) + 1.0
    style = "round,pad=0.0,rounding_size=0.8"
    ax.add_patch(
        FancyBboxPatch((x, y), w, h, boxstyle=style, fc="white", ec=color, lw=1.8, zorder=2)
    )
    ax.add_patch(
        FancyBboxPatch(
            (x, y + h - HEAD_H), w, HEAD_H, boxstyle=style, fc=color, ec=color, lw=1.8, zorder=3
        )
    )
    ax.text(x + w / 2, y + h - HEAD_H / 2, name, ha="center", va="center", color=text_color,
            fontsize=11, fontweight="bold", zorder=4)  # fmt: skip
    for i, line in enumerate(lines):
        ax.text(x + 1.2, y + h - HEAD_H - 1.4 - i * LINE_H, line, ha="left", va="center",
                fontsize=8.2, family="monospace", zorder=4)  # fmt: skip
    return (x, y, w, h)


def edge(b, side, frac=0.5):
    x, y, w, h = b
    return {
        "top": (x + w * frac, y + h),
        "bottom": (x + w * frac, y),
        "left": (x, y + h * frac),
        "right": (x + w, y + h * frac),
    }[side]


def link(ax, p, q, label_p="N", label_q="1"):
    ax.plot([p[0], q[0]], [p[1], q[1]], color="#444", lw=1.3, zorder=1)
    for pt, other, label in ((p, q, label_p), (q, p, label_q)):
        dx, dy = other[0] - pt[0], other[1] - pt[1]
        norm = max((dx * dx + dy * dy) ** 0.5, 1e-6)
        ax.text(pt[0] + dx / norm * 2.6 + 0.8, pt[1] + dy / norm * 2.6 + 0.4, label, fontsize=10,
                color="#111", fontweight="bold", ha="center", va="center", zorder=5,
                bbox=dict(fc="white", ec="none", pad=0.6))  # fmt: skip


def main() -> None:
    fig, ax = plt.subplots(figsize=(15, 10))
    ax.set_xlim(0, 150)
    ax.set_ylim(-24, 100)
    ax.axis("off")
    blue, green, grey = "#1F4E79", "#2E7D32", "#6B7280"

    dim_date = box(ax, 3, 66, 33, "dim_date", columns("dim_date"), green)
    dim_session = box(ax, 44, 70, 32, "dim_session", columns("dim_session"), green)
    dim_question = box(ax, 84, 74, 30, "dim_question", columns("dim_question"), green)
    dim_category = box(ax, 120, 80, 28, "dim_category", columns("dim_category"), green)
    fact = box(ax, 44, 2, 48, "fact_answers (fact)", FACT_LINES, blue)

    link(ax, edge(fact, "top", 0.1), edge(dim_date, "bottom", 0.7))
    link(ax, edge(fact, "top", 0.35), edge(dim_session, "bottom", 0.5))
    link(ax, edge(fact, "top", 0.65), edge(dim_question, "bottom", 0.5))
    link(ax, edge(fact, "top", 0.9), edge(dim_category, "bottom", 0.5))
    link(ax, edge(dim_session, "left", 0.5), edge(dim_date, "right", 0.5))
    link(ax, edge(dim_question, "right", 0.5), edge(dim_category, "left", 0.5))

    anomalies = box(ax, 100, 2, 47, "anomalies", columns("anomalies"), "#B45309")
    link(ax, edge(anomalies, "left", 0.5), edge(fact, "right", 0.3))
    box(ax, 3, -23, 33, "load_log", columns("load_log"), grey)
    box(ax, 44, -23, 33, "llm_cache", columns("llm_cache"), grey)
    box(ax, 85, -23, 36, "generated_questions", columns("generated_questions"), grey)

    ax.text(75, 97.5, "Interview Prep Analytics - star schema", ha="center", fontsize=15,
            fontweight="bold")  # fmt: skip
    ax.text(75, 94.8, "blue = fact, green = dimensions, orange = checks output, grey = support "
            "tables (incremental loads, response cache, generated questions)", ha="center",
            fontsize=9, color="#444")  # fmt: skip
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=140, bbox_inches="tight", facecolor="white")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
