from plextranslator.plex_client import streams_of_type


class _Stream:
    def __init__(self, stream_type, code):
        self.streamType = stream_type
        self.languageCode = code


class _PartWithMethods:
    """Mirrors plexapi MediaPart: audioStreams()/subtitleStreams() methods."""

    def __init__(self, streams):
        self._streams = streams

    def audioStreams(self):
        return [s for s in self._streams if s.streamType == 2]

    def subtitleStreams(self):
        return [s for s in self._streams if s.streamType == 3]


class _PartWithAttr:
    """Older/edge shape: only a .streams list attribute (no methods)."""

    def __init__(self, streams):
        self.streams = streams


def test_streams_of_type_uses_typed_method():
    part = _PartWithMethods([_Stream(2, "kor"), _Stream(3, "eng"), _Stream(2, "jpn")])
    audio = streams_of_type(part, 2, "audioStreams")
    subs = streams_of_type(part, 3, "subtitleStreams")
    assert [s.languageCode for s in audio] == ["kor", "jpn"]
    assert [s.languageCode for s in subs] == ["eng"]


def test_streams_of_type_falls_back_to_attribute():
    part = _PartWithAttr([_Stream(2, "kor"), _Stream(3, "eng")])
    assert [s.languageCode for s in streams_of_type(part, 2, "audioStreams")] == ["kor"]
    assert [s.languageCode for s in streams_of_type(part, 3, "subtitleStreams")] == ["eng"]


def test_streams_of_type_handles_missing_streams():
    class _Empty:
        pass

    assert streams_of_type(_Empty(), 2, "audioStreams") == []
