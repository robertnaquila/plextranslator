from plextranslator.plex_client import (
    is_target_language,
    normalize_lang,
    pick_target_audio_index,
)


def test_normalize_lang():
    assert normalize_lang(" KO ") == "ko"
    assert normalize_lang(None) == ""


def test_is_target_language():
    assert is_target_language("ko")
    assert is_target_language("kor")
    assert is_target_language("ja")
    assert is_target_language("jpn")
    assert not is_target_language("en")
    assert not is_target_language(None)


def test_pick_target_audio_index_first_match():
    # English at 0, Korean at 1 -> pick 1.
    assert pick_target_audio_index(["eng", "kor", "jpn"]) == 1


def test_pick_target_audio_index_none():
    assert pick_target_audio_index(["eng", "fra"]) is None


def test_pick_target_audio_index_japanese_first():
    assert pick_target_audio_index(["jpn", "eng"]) == 0
