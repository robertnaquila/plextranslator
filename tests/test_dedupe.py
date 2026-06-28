from plextranslator.dedupe import (
    CaptionAccumulator,
    overlap_count,
    tokenize,
    trailing_caption,
)


def test_overlap_count_basic():
    prev = tokenize("he is running away")
    new = tokenize("away from the house")
    assert overlap_count(prev, new, 12) == 1


def test_overlap_count_multiword():
    prev = tokenize("we have to get out of here")
    new = tokenize("out of here right now")
    assert overlap_count(prev, new, 12) == 3


def test_overlap_count_case_and_punctuation_insensitive():
    prev = tokenize("Run away!")
    new = tokenize("away, we must go")
    assert overlap_count(prev, new, 12) == 1


def test_overlap_count_none():
    assert overlap_count(tokenize("hello there"), tokenize("general kenobi"), 12) == 0
    assert overlap_count([], tokenize("x"), 12) == 0
    assert overlap_count(tokenize("x"), [], 12) == 0


def test_overlap_count_respects_max():
    prev = tokenize("get out of here")
    new = tokenize("out of here now")
    # real contiguous overlap is "out of here" (3 tokens)
    assert overlap_count(prev, new, 3) == 3
    # capping below the real overlap can't catch a partial prefix -> 0
    # (so max_overlap must be set >= the expected boundary overlap)
    assert overlap_count(prev, new, 2) == 0


def test_overlap_count_ignores_pure_punctuation():
    # a lone punctuation token normalizes to "" and must not count as a match
    assert overlap_count(["—"], ["—", "hello"], 12) == 0


def test_accumulator_dedupes_overlap():
    acc = CaptionAccumulator(max_sentences=5, max_words=100)
    acc.add("we have to get out of here")
    out = acc.add("out of here right now")
    # "out of here" should appear once, not twice
    assert out == "we have to get out of here right now"


def test_accumulator_appends_when_no_overlap():
    acc = CaptionAccumulator(max_sentences=5, max_words=100)
    acc.add("hello there")
    out = acc.add("general kenobi")
    assert out == "hello there general kenobi"


def test_accumulator_empty_window_is_noop():
    acc = CaptionAccumulator(max_sentences=5, max_words=100)
    acc.add("something happened")
    assert acc.add("   ") == "something happened"


def test_trailing_caption_keeps_last_sentences():
    words = tokenize("First sentence here. Second one now. Third and final one.")
    out = trailing_caption(words, max_sentences=2, max_words=100)
    assert out == "Second one now. Third and final one."


def test_trailing_caption_word_cap():
    words = tokenize(" ".join(str(i) for i in range(50)))  # one long run-on, no punctuation
    out = trailing_caption(words, max_sentences=2, max_words=5)
    assert out.split() == ["45", "46", "47", "48", "49"]


def test_accumulator_display_rolls_forward():
    acc = CaptionAccumulator(max_sentences=1, max_words=100)
    acc.add("Hello world.")
    acc.add("How are you?")
    # only the last sentence is displayed
    assert acc.display() == "How are you?"


def test_accumulator_reset():
    acc = CaptionAccumulator()
    acc.add("some words here")
    acc.reset()
    assert acc.display() == ""
