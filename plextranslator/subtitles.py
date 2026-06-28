"""Subtitle data model and SRT/WebVTT (de)serialization.

Pure module — no third-party dependencies — so it is trivially unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional


@dataclass
class Cue:
    """A single subtitle cue with timing in seconds."""

    start: float
    end: float
    text: str

    def shifted(self, offset: float) -> "Cue":
        """Return a copy moved forward by ``offset`` seconds (never below 0)."""
        return Cue(
            start=max(0.0, self.start + offset),
            end=max(0.0, self.end + offset),
            text=self.text,
        )


def format_timestamp(seconds: float, *, vtt: bool = False) -> str:
    """Format ``seconds`` as an SRT (``HH:MM:SS,mmm``) or VTT (``HH:MM:SS.mmm``)
    timestamp. Negative values clamp to zero."""
    if seconds < 0:
        seconds = 0.0
    millis_total = int(round(seconds * 1000))
    hours, millis_total = divmod(millis_total, 3_600_000)
    minutes, millis_total = divmod(millis_total, 60_000)
    secs, millis = divmod(millis_total, 1000)
    sep = "." if vtt else ","
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{millis:03d}"


def parse_timestamp(value: str) -> float:
    """Parse an SRT or VTT timestamp into seconds."""
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours, minutes, seconds = "0", parts[0], parts[1]
    else:
        raise ValueError(f"Unrecognized timestamp: {value!r}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def to_srt(cues: Iterable[Cue]) -> str:
    """Render cues to an SRT document. Empty-text cues are dropped."""
    blocks: List[str] = []
    index = 1
    for cue in cues:
        text = cue.text.strip()
        if not text:
            continue
        blocks.append(
            f"{index}\n"
            f"{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}\n"
            f"{text}\n"
        )
        index += 1
    return "\n".join(blocks)


def to_vtt(cues: Iterable[Cue]) -> str:
    """Render cues to a WebVTT document."""
    blocks: List[str] = ["WEBVTT\n"]
    for cue in cues:
        text = cue.text.strip()
        if not text:
            continue
        blocks.append(
            f"{format_timestamp(cue.start, vtt=True)} --> "
            f"{format_timestamp(cue.end, vtt=True)}\n"
            f"{text}\n"
        )
    return "\n".join(blocks)


_SRT_TIME_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})"
)


def parse_srt(text: str) -> List[Cue]:
    """Parse an SRT document into cues. Tolerant of VTT-style timestamps."""
    cues: List[Cue] = []
    # Split on blank lines into blocks.
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip() != ""]
        if not lines:
            continue
        # Optional leading numeric index.
        if lines[0].strip().isdigit():
            lines = lines[1:]
        if not lines:
            continue
        match = _SRT_TIME_RE.search(lines[0])
        if not match:
            continue
        start = parse_timestamp(match.group(1))
        end = parse_timestamp(match.group(2))
        body = "\n".join(lines[1:]).strip()
        cues.append(Cue(start=start, end=end, text=body))
    return cues


def cue_at(cues: List[Cue], t: float) -> Optional[Cue]:
    """Return the cue active at time ``t`` seconds, or None.

    ``cues`` is assumed sorted by start time. If cues overlap, the last one whose
    span contains ``t`` wins. Used by the web overlay to pick the line to show for
    the current playhead.
    """
    match: Optional[Cue] = None
    for cue in cues:
        if cue.start <= t < cue.end:
            match = cue
        elif cue.start > t:
            break
    return match


def merge_cues(existing: List[Cue], new: List[Cue]) -> List[Cue]:
    """Merge ``new`` cues into ``existing``, replacing any that overlap the new
    span and keeping the result sorted by start time.

    Used by live mode: each freshly transcribed chunk replaces whatever was
    previously generated for its time window so re-processing is idempotent.
    """
    if not new:
        return sorted(existing, key=lambda c: c.start)
    span_start = min(c.start for c in new)
    span_end = max(c.end for c in new)
    kept = [c for c in existing if c.end <= span_start or c.start >= span_end]
    return sorted(kept + list(new), key=lambda c: c.start)
