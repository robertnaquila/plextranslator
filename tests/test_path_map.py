from plextranslator.plex_client import parse_path_map, remap_path


def test_parse_path_map_basic():
    pairs = parse_path_map("/volume1/video=>/mnt/plex")
    assert pairs == [("/volume1/video", "/mnt/plex")]


def test_parse_path_map_multiple_and_sorted_by_specificity():
    spec = "/volume1=>/a; /volume1/video=>/b"
    pairs = parse_path_map(spec)
    # longest source prefix first
    assert pairs[0] == ("/volume1/video", "/b")
    assert pairs[1] == ("/volume1", "/a")


def test_parse_path_map_ignores_garbage():
    assert parse_path_map("") == []
    assert parse_path_map("no-arrow-here") == []
    assert parse_path_map("  =>  ") == []


def test_remap_path_rewrites_prefix():
    pairs = parse_path_map("/volume1/video=>/mnt/plex")
    assert (
        remap_path("/volume1/video/Korean/Train.mkv", pairs)
        == "/mnt/plex/Korean/Train.mkv"
    )


def test_remap_path_trailing_slash_normalized():
    pairs = parse_path_map("/volume1/video/=>/mnt/plex/")
    assert remap_path("/volume1/video/a.mkv", pairs) == "/mnt/plex/a.mkv"


def test_remap_path_no_match_unchanged():
    pairs = parse_path_map("/volume1/video=>/mnt/plex")
    assert remap_path("/other/path/a.mkv", pairs) == "/other/path/a.mkv"


def test_remap_path_no_partial_segment_match():
    # "/volume1/video2" must NOT match the "/volume1/video" prefix
    pairs = parse_path_map("/volume1/video=>/mnt/plex")
    assert remap_path("/volume1/video2/a.mkv", pairs) == "/volume1/video2/a.mkv"


def test_remap_path_most_specific_wins():
    pairs = parse_path_map("/volume1=>/a; /volume1/video=>/b")
    assert remap_path("/volume1/video/x.mkv", pairs) == "/b/x.mkv"
    assert remap_path("/volume1/music/y.mp3", pairs) == "/a/music/y.mp3"


def test_remap_path_empty_map_is_noop():
    assert remap_path("/volume1/video/a.mkv", []) == "/volume1/video/a.mkv"
