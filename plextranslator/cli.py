"""Command-line interface for plextranslator."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__
from .config import Config
from .logging_conf import configure_logging


def _add_common_plex_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--plex-url", dest="plex_baseurl", help="Plex base URL.")
    parser.add_argument("--plex-token", dest="plex_token", help="Plex auth token.")
    parser.add_argument(
        "--path-map",
        dest="path_map",
        help="Rewrite Plex media paths to local mounts, e.g. "
        "'/volume1/video=>/mnt/plex' (';'-separated for multiple). "
        "Use when running off the Plex host.",
    )
    parser.add_argument(
        "--backend",
        choices=["faster-whisper", "whisper.cpp"],
        help="Transcription backend (whisper.cpp runs on non-AVX CPUs like a NAS).",
    )
    parser.add_argument(
        "--model", dest="whisper_model", help="Whisper model size (e.g. large-v3)."
    )
    parser.add_argument("--device", help="auto | cpu | cuda.")
    parser.add_argument("--compute-type", dest="compute_type", help="Whisper compute type.")
    parser.add_argument(
        "--whisper-cpp-bin", dest="whisper_cpp_bin",
        help="Path to the whisper.cpp 'whisper-cli' binary.",
    )
    parser.add_argument(
        "--whisper-cpp-model", dest="whisper_cpp_model",
        help="Path to a whisper.cpp ggml model (e.g. /models/ggml-small.bin).",
    )
    parser.add_argument(
        "--whisper-cpp-threads", dest="whisper_cpp_threads", type=int,
        help="Threads for whisper.cpp (0 = its default).",
    )
    parser.add_argument(
        "--use-llm",
        dest="use_llm",
        action="store_true",
        default=None,
        help="Refine Whisper output with Claude for more natural English.",
    )
    parser.add_argument(
        "--anthropic-model", dest="anthropic_model", help="Claude model for refinement."
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plextranslator",
        description=(
            "Generate real-time English subtitles for Korean & Japanese Plex media."
        ),
    )
    parser.add_argument("--version", action="version", version=f"plextranslator {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    # live
    p_live = sub.add_parser("live", help="Follow the active playback session.")
    _add_common_plex_args(p_live)
    p_live.add_argument(
        "--chunk-seconds", dest="chunk_seconds", type=int, help="Seconds per chunk."
    )
    p_live.add_argument(
        "--lead-seconds", dest="lead_seconds", type=int, help="Seconds to stay ahead."
    )
    p_live.add_argument(
        "--poll-interval", dest="poll_interval", type=int, help="Session poll interval."
    )

    # web
    p_web = sub.add_parser(
        "web", help="Serve a browser subtitle overlay synced to Plex playback."
    )
    _add_common_plex_args(p_web)
    p_web.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1).")
    p_web.add_argument("--port", type=int, default=8765, help="Bind port (default 8765).")
    p_web.add_argument(
        "--chunk-seconds", dest="chunk_seconds", type=int, help="Seconds per chunk."
    )
    p_web.add_argument(
        "--lead-seconds", dest="lead_seconds", type=int, help="Seconds to stay ahead."
    )
    p_web.add_argument(
        "--poll-interval", dest="poll_interval", type=int, help="Session poll interval."
    )

    # capture (system-audio -> live captions; works for Netflix, any browser video)
    p_cap = sub.add_parser(
        "capture",
        help="Live English captions from system audio (Netflix & any streaming). No Plex needed.",
    )
    p_cap.add_argument("--model", dest="whisper_model", help="Whisper model size.")
    p_cap.add_argument("--device", help="Whisper compute device: auto | cpu | cuda.")
    p_cap.add_argument("--compute-type", dest="compute_type", help="Whisper compute type.")
    p_cap.add_argument(
        "--use-llm", dest="use_llm", action="store_true", default=None,
        help="Refine captions with Claude.",
    )
    p_cap.add_argument("--anthropic-model", dest="anthropic_model", help="Claude model.")
    p_cap.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    p_cap.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1).")
    p_cap.add_argument("--port", type=int, default=8765, help="Bind port (default 8765).")
    p_cap.add_argument(
        "--audio-device",
        dest="audio_device",
        help="ffmpeg input device (a loopback/monitor source, NOT a mic). "
        "Default is platform-specific.",
    )
    p_cap.add_argument(
        "--audio-format",
        dest="audio_format",
        help="ffmpeg input format (pulse | avfoundation | dshow | ...).",
    )
    p_cap.add_argument(
        "--window-seconds", dest="window_seconds", type=float, default=6.0,
        help="Audio window translated at a time (default 6).",
    )
    p_cap.add_argument(
        "--overlap-seconds", dest="overlap_seconds", type=float, default=0.5,
        help="Overlap between windows so words aren't clipped (default 0.5).",
    )
    p_cap.add_argument(
        "--source-language", dest="source_language",
        help="Force source language (ko/ja); default: auto-detect.",
    )
    p_cap.add_argument(
        "--no-dedupe", dest="dedupe", action="store_false",
        help="Disable overlap de-duplication (show each window verbatim).",
    )

    # library
    p_lib = sub.add_parser("library", help="Batch-subtitle the KO/JA library.")
    _add_common_plex_args(p_lib)
    p_lib.add_argument(
        "--section",
        dest="sections",
        action="append",
        help="Restrict to a named library section (repeatable).",
    )
    p_lib.add_argument("--limit", type=int, help="Stop after N items.")
    p_lib.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Re-process items that already have English subs.",
    )
    p_lib.add_argument(
        "--dry-run", action="store_true", help="List what would be processed."
    )

    # file (process a local media file directly, no Plex)
    p_file = sub.add_parser("file", help="Translate one local media file to an .srt.")
    _add_common_plex_args(p_file)
    p_file.add_argument("media", help="Path to a local media file.")
    p_file.add_argument("-o", "--output", help="Output .srt path.")
    p_file.add_argument(
        "--source-language",
        dest="source_language",
        help="Force source language (ko/ja); default: auto-detect.",
    )

    # config (print resolved config / validate)
    sub.add_parser("config", help="Print resolved configuration and validate it.")

    return parser


def _config_from_args(args: argparse.Namespace) -> Config:
    return Config.from_env().merge(
        plex_baseurl=getattr(args, "plex_baseurl", None),
        plex_token=getattr(args, "plex_token", None),
        path_map=getattr(args, "path_map", None),
        backend=getattr(args, "backend", None),
        whisper_model=getattr(args, "whisper_model", None),
        device=getattr(args, "device", None),
        compute_type=getattr(args, "compute_type", None),
        whisper_cpp_bin=getattr(args, "whisper_cpp_bin", None),
        whisper_cpp_model=getattr(args, "whisper_cpp_model", None),
        whisper_cpp_threads=getattr(args, "whisper_cpp_threads", None),
        use_llm=getattr(args, "use_llm", None),
        anthropic_model=getattr(args, "anthropic_model", None),
        chunk_seconds=getattr(args, "chunk_seconds", None),
        lead_seconds=getattr(args, "lead_seconds", None),
        poll_interval=getattr(args, "poll_interval", None),
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(getattr(args, "verbose", False))
    config = _config_from_args(args)

    if args.command == "config":
        return _cmd_config(config)
    if args.command == "file":
        return _cmd_file(config, args)
    if args.command == "capture":
        return _cmd_capture(config, args)

    # live and library both need a valid Plex connection.
    problems = config.validate()
    if problems:
        for problem in problems:
            print(f"config error: {problem}", file=sys.stderr)
        return 2

    if args.command == "live":
        return _cmd_live(config)
    if args.command == "web":
        return _cmd_web(config, args)
    if args.command == "library":
        return _cmd_library(config, args)

    parser.error(f"unknown command {args.command!r}")
    return 2


def _cmd_config(config: Config) -> int:
    redacted = config.merge(
        plex_token="***" if config.plex_token else "",
        anthropic_api_key="***" if config.anthropic_api_key else "",
    )
    for field, value in vars(redacted).items():
        print(f"{field} = {value}")
    problems = config.validate()
    if problems:
        print("\nProblems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nConfiguration looks OK.")
    return 0


def _cmd_file(config: Config, args: argparse.Namespace) -> int:
    import os

    from .pipeline import Pipeline
    from .subtitles import to_srt

    if not os.path.exists(args.media):
        print(f"No such file: {args.media}", file=sys.stderr)
        return 2
    output = args.output or (os.path.splitext(args.media)[0] + f".{config.subtitle_language}.srt")
    pipeline = Pipeline(config)
    cues = pipeline.process_file(args.media, source_language=args.source_language)
    if not cues:
        print("No speech was translated.", file=sys.stderr)
        return 1
    with open(output, "w", encoding="utf-8") as fh:
        fh.write(to_srt(cues))
    print(f"Wrote {output} ({len(cues)} cues)")
    return 0


def _cmd_live(config: Config) -> int:
    from .live import LiveSubtitler

    print("Watching for Korean/Japanese playback... (Ctrl-C to stop)")
    subtitler = LiveSubtitler(config)
    try:
        subtitler.run_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def _cmd_capture(config: Config, args: argparse.Namespace) -> int:
    from .capture import run_capture

    try:
        run_capture(
            config,
            host=args.host,
            port=args.port,
            device=args.audio_device,
            input_format=args.audio_format,
            window_seconds=args.window_seconds,
            overlap_seconds=args.overlap_seconds,
            source_language=args.source_language,
            dedupe=args.dedupe,
        )
    except KeyboardInterrupt:
        pass
    return 0


def _cmd_web(config: Config, args: argparse.Namespace) -> int:
    from .web import run_web

    try:
        run_web(config, host=args.host, port=args.port)
    except KeyboardInterrupt:
        pass
    return 0


def _cmd_library(config: Config, args: argparse.Namespace) -> int:
    from .library import run_library

    count = run_library(
        config,
        section_titles=args.sections,
        limit=args.limit,
        skip_existing=args.skip_existing,
        dry_run=args.dry_run,
    )
    print(f"{'Would subtitle' if args.dry_run else 'Subtitled'} {count} item(s).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
