from plextranslator.subtitles import Cue
from plextranslator.translator import Refiner, _parse_json_array


def test_parse_json_array_plain():
    assert _parse_json_array('["a", "b"]') == ["a", "b"]


def test_parse_json_array_with_fence():
    fenced = '```json\n["x", "y"]\n```'
    assert _parse_json_array(fenced) == ["x", "y"]


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Msg:
    def __init__(self, text):
        self.content = [_Block(text)]


class _FakeMessages:
    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Msg(self._reply)


class _FakeClient:
    def __init__(self, reply):
        self.messages = _FakeMessages(reply)


def _refiner_with(reply):
    r = Refiner(api_key="k", model="claude-opus-4-8")
    r._client = _FakeClient(reply)
    return r


def test_refine_replaces_text_keeps_timing():
    cues = [Cue(0, 1, "rough one"), Cue(1, 2, "rough two")]
    r = _refiner_with('["nice one", "nice two"]')
    out = r.refine(cues)
    assert [c.text for c in out] == ["nice one", "nice two"]
    # timings preserved
    assert out[0].start == 0 and out[1].end == 2


def test_refine_count_mismatch_keeps_raw():
    cues = [Cue(0, 1, "a"), Cue(1, 2, "b")]
    r = _refiner_with('["only one"]')  # wrong count
    out = r.refine(cues)
    assert [c.text for c in out] == ["a", "b"]


def test_refine_error_keeps_raw():
    cues = [Cue(0, 1, "a")]
    r = _refiner_with("not json at all")
    out = r.refine(cues)
    assert [c.text for c in out] == ["a"]


def test_refine_empty_is_noop():
    r = Refiner(api_key="k")
    assert r.refine([]) == []
