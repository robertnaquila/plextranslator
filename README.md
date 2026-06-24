# plextranslator

**Real-time English subtitles for Korean & Japanese shows on Plex.**

Watching Korean or Japanese movies on Plex with no English subtitles and no
English dub? `plextranslator` listens to the original audio, transcribes and
translates it to English with [Whisper](https://github.com/openai/whisper)
(via [faster-whisper](https://github.com/SYSTRAN/faster-whisper)), optionally
polishes the wording with Claude, and feeds the result back into Plex as a
subtitle track — so you can understand what you're watching.

It works two ways:

| Mode | What it does | When to use it |
|------|--------------|----------------|
| **`live`** | Follows whatever Korean/Japanese title you're currently playing, generating English subtitles in chunks that race *ahead* of the playhead and upload to Plex as you watch. | "Press play and start understanding it now." |
| **`web`** | Serves a **browser subtitle overlay** synced to your Plex playback. Open it in a tab (Plex Web users especially), and live English subtitles appear in time with the video — no client-side subtitle support needed. | Watching in a browser / Plex Web. |
| **`library`** | Scans your Plex library and writes an English `.srt` next to every KO/JA movie/episode that lacks English subs (Plex auto-detects sidecar files). | Pre-translate a show/film before you sit down. |

> **Note on "real-time".** Whisper is not a word-by-word streaming model, so live
> mode is *near*-real-time: subtitles appear within a chunk (~1 min by default)
> of starting playback and then stay ahead of you. It's designed so you can
> start watching immediately and have subtitles catch up and lead.

---

## How it works

```
Plex ──(active session / library scan)──▶ media file path + KO/JA audio track
                                                      │
                                            ffmpeg: extract 16 kHz mono audio
                                                      │
                                  faster-whisper task=translate  →  English cues
                                                      │
                              (optional) Claude refines to natural English
                                                      │
                                   SRT  ──▶  sidecar file  +  upload to Plex
```

- **Whisper's `translate` task** turns speech in any language *directly* into
  English — one pass gets you English subtitles from Korean/Japanese audio.
- **The optional Claude pass** (`--use-llm`) rewrites Whisper's sometimes-literal
  output into fluent, idiomatic subtitle English while preserving cue count and
  timing.

---

## Requirements

- **Python 3.10+**
- **ffmpeg** on your `PATH` (`apt install ffmpeg` / `brew install ffmpeg`)
- A **Plex Media Server** and an
  [auth token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)
- For decent speed/quality on the larger Whisper models, an **NVIDIA GPU** helps
  a lot — but `small`/`medium` run fine on CPU.

## Install

```bash
git clone https://github.com/robertnaquila/plextranslator
cd plextranslator

# Core package + runtime deps (faster-whisper, plexapi):
pip install -e '.[run]'

# Optionally add Claude-based refinement:
pip install -e '.[run,llm]'
```

## Configure

Copy `.env.example` to `.env` and fill it in (or pass everything as flags):

```bash
cp .env.example .env
$EDITOR .env
```

At minimum set `PLEX_BASEURL` and `PLEX_TOKEN`. Check it:

```bash
plextranslator config
```

> The CLI reads environment variables; load your `.env` however you like
> (e.g. `set -a; . ./.env; set +a`).

## Usage

### Live — subtitle what you're watching now

```bash
plextranslator live
```

Start playing a Korean or Japanese title in any Plex client. `plextranslator`
detects the session, generates English subtitles ahead of your playhead, and
uploads them to the item. In your Plex client, switch the **Subtitles** track to
the newly uploaded English track; it keeps growing as you watch.

Tuning:

```bash
plextranslator live --chunk-seconds 45 --lead-seconds 180 --use-llm
```

### Web — subtitles in a browser (Plex Web & other browser players)

```bash
plextranslator web                       # serve at http://127.0.0.1:8765
plextranslator web --host 0.0.0.0 --port 9000 --use-llm
```

Then open `http://127.0.0.1:8765/` in a browser and start playing a Korean or
Japanese title in Plex (e.g. in another tab via Plex Web). The overlay page shows
the current English line, synced to your real playback position.

How it stays in sync: a background poller tracks the active Plex session's
playhead (interpolating between polls for smooth, sub-second timing) while the
translation pipeline generates cues ahead of you. The page receives the current
line over Server-Sent Events. Controls let you bump the font size and nudge the
timing (±0.5 s) if it drifts.

Endpoints (stdlib-only HTTP server, no framework):

| Path | Purpose |
|------|---------|
| `/` | The subtitle overlay page (place it over your video). |
| `/events` | Server-Sent Events stream of the current line + state. |
| `/subtitles.vtt` | Live-growing WebVTT — load it as a `<track>` in any player. |
| `/state` | JSON snapshot (current line, playhead, status). |

> The overlay shows subtitles for whatever Plex is playing. To literally lay it
> *over* the video, run Plex Web and the overlay in separate windows and position
> the overlay on top, or use the `/subtitles.vtt` track in a player that supports
> external subtitle URLs.

### Library — pre-translate KO/JA media

```bash
# See what would be processed:
plextranslator library --dry-run

# Translate everything missing English subs:
plextranslator library

# Just one section, first 5 items, with Claude polishing:
plextranslator library --section "Korean Films" --limit 5 --use-llm
```

Sidecar `.srt` files are written next to each media file (e.g.
`Movie (2019).en.srt`), which Plex auto-detects on the next scan. If the sidecar
can't be written (read-only mount), it falls back to `output_dir` and uploads
the subtitle to the Plex item via the API.

### File — translate a single local file (no Plex)

```bash
plextranslator file /path/to/movie.mkv -o movie.en.srt
plextranslator file movie.mkv --source-language ja   # force the source language
```

## Choosing a Whisper model

| Model | Quality | Speed | Notes |
|-------|---------|-------|-------|
| `large-v3` | best | slowest | Recommended on a GPU. Best KO/JA→EN. |
| `medium` | very good | moderate | Good GPU/CPU compromise. |
| `small` | good | fast | Reasonable on CPU. |
| `base` / `tiny` | rough | fastest | For testing only. |

```bash
plextranslator live --model medium --device cpu
```

## Why optionally use Claude?

Whisper's built-in translation is good but can read literally ("It is a thing
that I must do") where a person would write "I have to do this." The `--use-llm`
pass sends batches of lines to Claude (default `claude-opus-4-8`) and asks for
natural subtitle English, one line in → one line out, so timings stay aligned.
It's best-effort: if the API call fails or returns the wrong number of lines, the
original Whisper text is kept.

## Development

```bash
pip install -e '.[dev]'
pytest            # pure-logic tests (no GPU / Plex / network needed)
ruff check .
```

The codebase is split so the pure logic — subtitle (de)serialization, ffmpeg
command building, chunk planning, language detection, live scheduling — has no
heavy dependencies and is fully unit-tested. The GPU/network integrations
(faster-whisper, plexapi, anthropic) are imported lazily.

```
plextranslator/
  config.py        # env/flag configuration
  subtitles.py     # Cue model, SRT/VTT (de)serialization, cue merging
  audio.py         # ffmpeg command builder + runner
  transcriber.py   # faster-whisper wrapper (task=translate)
  translator.py    # optional Claude refinement
  plex_client.py   # discover KO/JA media, follow sessions, upload subs
  pipeline.py      # extract → translate → refine; chunk planner
  library.py       # batch mode
  live.py          # live/follow-the-playhead mode
  web.py           # browser overlay server (SSE) synced to Plex playback
  cli.py           # argparse entrypoint
```

## Limitations & notes

- Live mode re-uploads the growing `.srt` to the Plex item each chunk; you may
  need to (re)select the English subtitle track in your client to see updates.
- Quality depends on the Whisper model and the audio (music/SFX-heavy scenes are
  harder). `large-v3` on a GPU is dramatically better than `tiny` on CPU.
- Translation is machine-generated — great for understanding a show, not a
  substitute for professional subtitles.

## License

MIT
