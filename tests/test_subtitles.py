from plextranslator.subtitles import (
    Cue,
    format_timestamp,
    merge_cues,
    parse_srt,
    parse_timestamp,
    to_srt,
    to_vtt,
)


def test_format_timestamp_srt():
    assert format_timestamp(0) == "00:00:00,000"
    assert format_timestamp(1.5) == "00:00:01,500"
    assert format_timestamp(3661.234) == "01:01:01,234"


def test_format_timestamp_vtt_and_negative_clamp():
    assert format_timestamp(1.5, vtt=True) == "00:00:01.500"
    assert format_timestamp(-5) == "00:00:00,000"


def test_parse_timestamp_roundtrip():
    assert parse_timestamp("01:01:01,234") == 3661.234
    assert parse_timestamp("00:00:01.500") == 1.5


def test_to_srt_numbers_and_skips_empty():
    cues = [
        Cue(0, 1, "Hello"),
        Cue(1, 2, "   "),  # dropped
        Cue(2, 3, "World"),
    ]
    srt = to_srt(cues)
    # Two blocks, renumbered 1 and 2 (empty cue skipped).
    assert "1\n00:00:00,000 --> 00:00:01,000\nHello" in srt
    assert "2\n00:00:02,000 --> 00:00:03,000\nWorld" in srt
    assert "3\n" not in srt


def test_to_vtt_has_header():
    assert to_vtt([Cue(0, 1, "Hi")]).startswith("WEBVTT")


def test_parse_srt_roundtrip():
    cues = [Cue(0, 1.5, "Line one"), Cue(2, 3, "Line two")]
    parsed = parse_srt(to_srt(cues))
    assert len(parsed) == 2
    assert parsed[0].text == "Line one"
    assert parsed[0].start == 0
    assert parsed[0].end == 1.5


def test_cue_shifted_clamps_at_zero():
    c = Cue(1, 2, "x").shifted(-5)
    assert c.start == 0
    assert c.end == 0


def test_merge_cues_replaces_overlapping_window():
    existing = [Cue(0, 5, "old-a"), Cue(5, 10, "old-b"), Cue(20, 25, "keep")]
    new = [Cue(4, 8, "new")]
    merged = merge_cues(existing, new)
    texts = [c.text for c in merged]
    # old-a (0-5) and old-b (5-10) overlap the new span (4-8) -> replaced.
    assert "old-a" not in texts
    assert "old-b" not in texts
    assert "new" in texts
    assert "keep" in texts
    # sorted by start
    assert merged == sorted(merged, key=lambda c: c.start)


def test_merge_cues_empty_new_returns_sorted():
    existing = [Cue(5, 6, "b"), Cue(0, 1, "a")]
    merged = merge_cues(existing, [])
    assert [c.text for c in merged] == ["a", "b"]
