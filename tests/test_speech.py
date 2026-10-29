from ipa.speech import compute_speech_metrics, count_fillers, pace_label


def test_filler_words_ported_from_typescript_case():
    f = count_fillers(
        "Um, so I basically led the, uh, migration. You know, it was like hard. "
        "I would like to add that it looks like a win."
    )
    assert f["um"] == 1
    assert f["uh"] == 1
    assert f["basically"] == 1
    assert f["you know"] == 1
    assert f["like"] == 1  # only the filler "like" counts
    assert f["so"] == 1


def test_pace_ported_from_typescript_case():
    m = compute_speech_metrics("one two three four five six seven eight nine ten", 5)
    assert m.words == 10
    assert m.words_per_minute == 120


def test_so_only_counts_at_clause_start():
    assert count_fillers("so we ran it").get("so") == 1
    assert count_fillers("and so we ran it").get("so") == 1
    assert "so" not in count_fillers("we ran it so fast")


def test_like_exceptions():
    assert "like" not in count_fillers("it looks like rain")
    assert "like" not in count_fillers("we like the design")
    assert count_fillers("it was, like, really slow").get("like") == 1


def test_phrase_fillers_are_removed_before_single_words():
    f = count_fillers("It was kind of slow and sort of messy, you know")
    assert f == {"you know": 1, "kind of": 1, "sort of": 1}


def test_empty_text_and_zero_duration():
    m = compute_speech_metrics("   ", 30)
    assert (m.words, m.words_per_minute, m.filler_count) == (0, 0, 0)
    assert compute_speech_metrics("hello there", 0).words_per_minute == 0


def test_rounding_halves_up_like_javascript():
    # 5 words in 120 s -> 2.5 wpm -> JS Math.round gives 3
    assert compute_speech_metrics("a b c d e", 120).words_per_minute == 3


def test_pace_label():
    assert pace_label(0) == "n/a"
    assert pace_label(100) == "slow"
    assert pace_label(140) == "good"
    assert pace_label(180) == "fast"
