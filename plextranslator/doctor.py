"""Preflight checks: ``plextranslator doctor``.

Verifies the environment before you start a real run: ffmpeg, the selected
transcription backend (faster-whisper import, or the whisper.cpp binary + model),
Plex connectivity, optional LLM refinement, config validity, and a writable
output directory.

Each check is a small function with its external dependency injected, so the
logic is unit-testable without ffmpeg, a Plex server, or a network.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Callable, List, Optional

from .config import Config

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover - defensive
        return False


# --- individual checks ---------------------------------------------------


def check_config(config: Config) -> Check:
    problems = config.validate()
    if problems:
        return Check("config", FAIL, "; ".join(problems))
    return Check("config", OK, "valid")


def check_ffmpeg(which: Callable[[str], Optional[str]] = shutil.which) -> Check:
    path = which("ffmpeg")
    if path:
        return Check("ffmpeg", OK, path)
    return Check("ffmpeg", FAIL, "not found on PATH — install ffmpeg")


def check_backend(
    config: Config,
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
    exists: Callable[[str], bool] = os.path.exists,
    faster_whisper_available: Optional[bool] = None,
) -> Check:
    if config.backend == "whisper.cpp":
        resolved = which(config.whisper_cpp_bin)
        if not resolved and exists(config.whisper_cpp_bin):
            resolved = config.whisper_cpp_bin
        if resolved:
            return Check("whisper.cpp binary", OK, resolved)
        return Check(
            "whisper.cpp binary",
            FAIL,
            f"{config.whisper_cpp_bin!r} not found — build whisper.cpp and set "
            "--whisper-cpp-bin",
        )
    avail = (
        faster_whisper_available
        if faster_whisper_available is not None
        else _module_available("faster_whisper")
    )
    if avail:
        return Check("faster-whisper", OK, "import available")
    return Check(
        "faster-whisper",
        FAIL,
        "not installed — pip install '.[run]' (or use --backend whisper.cpp)",
    )


def check_whisper_cpp_model(
    config: Config, *, exists: Callable[[str], bool] = os.path.exists
) -> Check:
    if config.backend != "whisper.cpp":
        return Check("whisper.cpp model", SKIP, "backend is faster-whisper")
    if not config.whisper_cpp_model:
        return Check(
            "whisper.cpp model", FAIL, "no model set (--whisper-cpp-model)"
        )
    if exists(config.whisper_cpp_model):
        return Check("whisper.cpp model", OK, config.whisper_cpp_model)
    return Check(
        "whisper.cpp model", FAIL, f"file not found: {config.whisper_cpp_model}"
    )


PlexFactory = Callable[[Config], object]


def check_plex(config: Config, *, factory: Optional[PlexFactory] = None) -> Check:
    if not config.plex_baseurl or not config.plex_token:
        return Check(
            "Plex connection",
            WARN,
            "PLEX_BASEURL/PLEX_TOKEN not set (fine for capture/file modes)",
        )
    if factory is None:
        from .plex_client import PlexClient

        factory = lambda c: PlexClient(  # noqa: E731
            c.plex_baseurl, c.plex_token, c.path_map
        )
    try:
        info = factory(config).server_info()
        detail = (
            f"{info['name']} (v{info['version']}), "
            f"{len(info['sections'])} libraries"
        )
        return Check("Plex connection", OK, detail)
    except Exception as exc:  # noqa: BLE001 - report any failure
        return Check("Plex connection", FAIL, f"could not connect: {exc}")


def check_llm(
    config: Config, *, anthropic_available: Optional[bool] = None
) -> Check:
    if not config.use_llm:
        return Check("LLM refinement", SKIP, "disabled")
    avail = (
        anthropic_available
        if anthropic_available is not None
        else _module_available("anthropic")
    )
    if not avail:
        return Check("LLM refinement", FAIL, "anthropic not installed ('.[llm]')")
    if not config.anthropic_api_key:
        return Check("LLM refinement", FAIL, "ANTHROPIC_API_KEY not set")
    return Check("LLM refinement", OK, f"model {config.anthropic_model}")


def check_output_dir(config: Config) -> Check:
    path = config.output_dir
    try:
        os.makedirs(path, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path, prefix=".plextranslator_write_test_")
        os.close(fd)
        os.remove(tmp)
        return Check("output dir", OK, f"{path} (writable)")
    except OSError as exc:
        return Check("output dir", FAIL, f"{path} not writable: {exc}")


# --- orchestration -------------------------------------------------------


def run_doctor(config: Config) -> List[Check]:
    """Run all checks in a sensible order and return the results."""
    return [
        check_config(config),
        check_ffmpeg(),
        check_backend(config),
        check_whisper_cpp_model(config),
        check_plex(config),
        check_llm(config),
        check_output_dir(config),
    ]


def exit_code(checks: List[Check]) -> int:
    """0 if no check failed, else 1. WARN/SKIP don't fail the run."""
    return 1 if any(c.status == FAIL for c in checks) else 0


_SYMBOLS = {OK: "✓", WARN: "!", FAIL: "✗", SKIP: "–"}


def render(checks: List[Check]) -> str:
    width = max((len(c.name) for c in checks), default=0)
    lines = []
    for c in checks:
        sym = _SYMBOLS.get(c.status, "?")
        line = f"  {sym} [{c.status:^4}] {c.name.ljust(width)}"
        if c.detail:
            line += f"  — {c.detail}"
        lines.append(line)
    summary = (
        "All checks passed."
        if exit_code(checks) == 0
        else "Some checks FAILED — fix the items above before running."
    )
    return "plextranslator doctor\n" + "\n".join(lines) + f"\n\n{summary}"
