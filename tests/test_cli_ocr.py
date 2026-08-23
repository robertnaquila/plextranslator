"""Tests for `ocr` CLI wiring: --region-cache persistence."""

import argparse

import pytest

from plextranslator import cli
from plextranslator.config import Config
from plextranslator.ocr import Region


def _base_args(**overrides):
    defaults = dict(
        region="1,2,3,4",
        select_region=False,
        host="127.0.0.1",
        port=8765,
        interval=0.4,
        ocr_backend="auto",
        source_language="ko",
        monitor_index=1,
        stable_frames=2,
        probe=False,
        tesseract_cmd=None,
        psm=6,
        save_frame=None,
        anthropic_model=None,
        region_cache=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_region_cache_written_for_typed_region(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("plextranslator.ocr.run_ocr", lambda config, **kw: calls.append(kw))

    cache_path = tmp_path / "region.txt"
    args = _base_args(region="10,20,300,80", region_cache=str(cache_path))
    rc = cli._cmd_ocr(Config(), args)

    assert rc == 0
    assert cache_path.read_text(encoding="utf-8") == "10,20,300,80"
    assert calls[0]["region_spec"] == "10,20,300,80"


def test_region_cache_written_for_picked_region(tmp_path, monkeypatch):
    monkeypatch.setattr("plextranslator.ocr.run_ocr", lambda config, **kw: None)
    monkeypatch.setattr(
        "plextranslator.ocr_picker.pick_region",
        lambda: Region(left=5, top=6, width=700, height=80),
    )

    cache_path = tmp_path / "region.txt"
    args = _base_args(select_region=True, region_cache=str(cache_path))
    rc = cli._cmd_ocr(Config(), args)

    assert rc == 0
    assert cache_path.read_text(encoding="utf-8") == "5,6,700,80"


def test_region_cache_creates_parent_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("plextranslator.ocr.run_ocr", lambda config, **kw: None)

    cache_path = tmp_path / "nested" / "dir" / "region.txt"
    args = _base_args(region_cache=str(cache_path))
    cli._cmd_ocr(Config(), args)

    assert cache_path.read_text(encoding="utf-8") == "1,2,3,4"


def test_region_cache_none_skips_write(monkeypatch, tmp_path):
    monkeypatch.setattr("plextranslator.ocr.run_ocr", lambda config, **kw: None)
    monkeypatch.chdir(tmp_path)
    args = _base_args(region_cache=None)
    rc = cli._cmd_ocr(Config(), args)
    assert rc == 0
    assert list(tmp_path.iterdir()) == []


def test_region_cache_write_failure_is_nonfatal(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("plextranslator.ocr.run_ocr", lambda config, **kw: None)

    # A path component that is a FILE, not a directory: os.makedirs must raise.
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    bad_cache = blocker / "region.txt"

    args = _base_args(region_cache=str(bad_cache))
    rc = cli._cmd_ocr(Config(), args)

    assert rc == 0  # OCR still proceeds
    assert "warning" in capsys.readouterr().err.lower()


def test_select_region_cancelled_returns_1(monkeypatch):
    monkeypatch.setattr("plextranslator.ocr_picker.pick_region", lambda: None)
    args = _base_args(select_region=True)
    assert cli._cmd_ocr(Config(), args) == 1


def test_select_region_runtime_error_returns_2(monkeypatch, capsys):
    def boom():
        raise RuntimeError("no display")

    monkeypatch.setattr("plextranslator.ocr_picker.pick_region", boom)
    args = _base_args(select_region=True)
    assert cli._cmd_ocr(Config(), args) == 2
    assert "no display" in capsys.readouterr().err
