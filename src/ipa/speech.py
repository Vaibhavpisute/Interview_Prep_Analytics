"""Delivery metrics for practice answers: pace and filler words.

Python port of the countFillers / computeSpeechMetrics / paceLabel functions from the
earlier TypeScript prototype, keeping the same rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SINGLE_FILLERS = [
    "um", "umm", "uh", "uhh", "erm", "er", "hmm",
    "basically", "actually", "literally", "like", "so",
]  # fmt: skip
PHRASE_FILLERS = ["you know", "i mean", "kind of", "sort of", "you see"]

# "so" counts only at the start of a clause.
_SO_PREV = {"and", "but", "um", "uh"}
# "like" does not count when it compares or states a wish ("looks like", "would like").
_LIKE_PREV = {
    "would", "looks", "look", "feel", "feels", "seems", "just", "i", "we", "they", "you",
    "things", "something",
}  # fmt: skip
_LIKE_NEXT = {"a", "an", "the", "this", "that", "to"}


@dataclass
class SpeechMetrics:
    words: int
    duration_sec: int
    words_per_minute: int
    filler_count: int
    fillers: dict[str, int] = field(default_factory=dict)


def count_fillers(text: str) -> dict[str, int]:
    cleaned = re.sub(r"[^\w']|_", " ", text.lower())
    lower = " " + re.sub(r"\s+", " ", cleaned) + " "
    counts: dict[str, int] = {}
    remaining = lower
    for phrase in PHRASE_FILLERS:
        pattern = f" {phrase} "
        # Overlapping matches share a space, so count like JS regex /g does (non-overlapping).
        n = len(re.findall(re.escape(pattern), remaining))
        if n:
            counts[phrase] = n
            remaining = remaining.replace(pattern, " ")
    words = [w for w in remaining.strip().split(" ") if w]
    for i, w in enumerate(words):
        if w not in SINGLE_FILLERS:
            continue
        prev = words[i - 1] if i > 0 else None
        nxt = words[i + 1] if i + 1 < len(words) else None
        if w == "so" and i != 0 and prev not in _SO_PREV:
            continue
        if w == "like" and (prev in _LIKE_PREV or nxt in _LIKE_NEXT):
            continue
        counts[w] = counts.get(w, 0) + 1
    return counts


def compute_speech_metrics(text: str, duration_sec: float) -> SpeechMetrics:
    stripped = text.strip()
    words = len(stripped.split()) if stripped else 0
    fillers = count_fillers(text)
    minutes = duration_sec / 60
    # round() in Python is banker's rounding; JS Math.round rounds halves up.
    wpm = int(words / minutes + 0.5) if minutes > 0 else 0
    return SpeechMetrics(
        words=words,
        duration_sec=int(duration_sec + 0.5),
        words_per_minute=wpm,
        filler_count=sum(fillers.values()),
        fillers=fillers,
    )


def pace_label(wpm: float) -> str:
    if not wpm:
        return "n/a"
    if wpm < 110:
        return "slow"
    if wpm > 170:
        return "fast"
    return "good"
