from plextranslator.audio import build_ffmpeg_cmd


def test_build_ffmpeg_cmd_basic():
    cmd = build_ffmpeg_cmd("in.mkv", "out.wav")
    assert cmd[0] == "ffmpeg"
    assert "-i" in cmd and cmd[cmd.index("-i") + 1] == "in.mkv"
    assert cmd[-1] == "out.wav"
    # mono, 16 kHz, pcm
    assert "1" == cmd[cmd.index("-ac") + 1]
    assert "16000" == cmd[cmd.index("-ar") + 1]
    assert "pcm_s16le" in cmd


def test_build_ffmpeg_cmd_window_seeks_before_input():
    cmd = build_ffmpeg_cmd("in.mkv", "out.wav", start=30, duration=60)
    ss_idx = cmd.index("-ss")
    i_idx = cmd.index("-i")
    assert ss_idx < i_idx, "input seek (-ss) must come before -i"
    assert cmd[ss_idx + 1] == "30.000"
    assert cmd[cmd.index("-t") + 1] == "60.000"


def test_build_ffmpeg_cmd_no_seek_when_start_zero():
    cmd = build_ffmpeg_cmd("in.mkv", "out.wav", start=0)
    assert "-ss" not in cmd


def test_build_ffmpeg_cmd_audio_track_mapping():
    cmd = build_ffmpeg_cmd("in.mkv", "out.wav", audio_track=1)
    assert "-map" in cmd
    assert cmd[cmd.index("-map") + 1] == "0:a:1"
