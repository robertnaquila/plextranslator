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
| **`capture`** | **Netflix & any browser streaming.** Captures the audio your computer is playing, translates it on the fly, and shows live English captions in the browser overlay. No Plex, no media file needed. | Netflix, Disney+, YouTube — anything with KO/JA audio. |
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

### Preflight check

Before a real run, `doctor` verifies your whole setup — ffmpeg, the selected
backend (faster-whisper import, or the whisper.cpp binary **and** model file),
Plex connectivity, optional LLM, config, and a writable output dir:

```bash
plextranslator doctor
# whisper.cpp backend on a NAS:
plextranslator doctor --backend whisper.cpp --whisper-cpp-model /models/ggml-small.bin
```

Example output:

```
plextranslator doctor
  ✓ [ OK ] config             — valid
  ✓ [ OK ] ffmpeg             — /usr/bin/ffmpeg
  ✓ [ OK ] whisper.cpp binary — /usr/local/bin/whisper-cli
  ✓ [ OK ] whisper.cpp model  — /models/ggml-small.bin
  ✓ [ OK ] Plex connection    — Tower (v1.40), 4 libraries
  – [SKIP] LLM refinement     — disabled
  ✓ [ OK ] output dir         — /out (writable)

All checks passed.
```

It exits non-zero if any check FAILs, so it's safe to gate a scheduled job on it.

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

### Capture — Netflix and any browser streaming

Streaming services like **Netflix** don't expose the media file (the audio is
DRM-protected) and there's no playback position to read — so the file-based modes
above can't touch them. Instead, `capture` listens to the audio your computer is
**playing** and translates it live:

```bash
plextranslator capture                      # serve overlay at http://127.0.0.1:8765
plextranslator capture --source-language ko # force Korean
plextranslator capture --use-llm --window-seconds 5
```

Open `http://127.0.0.1:8765/` in a browser, start your Korean/Japanese title on
Netflix (or anywhere), and English captions roll in with a few seconds' latency.
This needs no Plex and works for **any** app that plays audio.

> **Important: capture a loopback/"monitor" device, not your microphone** — or
> you'll transcribe the room instead of the show. You point ffmpeg at the device
> that mirrors your speaker output:

| OS | Setup | Example |
|----|-------|---------|
| **Linux** (PulseAudio/PipeWire) | Use your output's `.monitor` source. List them with `pactl list short sources`. | `plextranslator capture --audio-format pulse --audio-device "alsa_output.pci-0000_00_1f.3.analog-stereo.monitor"` |
| **macOS** | Install a virtual loopback like [BlackHole](https://existential.audio/blackhole/), route system output to it, then capture its avfoundation index (from `ffmpeg -f avfoundation -list_devices true -i ""`). | `plextranslator capture --audio-format avfoundation --audio-device ":2"` |
| **Windows** | Enable **Stereo Mix** (Sound → Recording) or install [VB-CABLE](https://vb-audio.com/Cable/). | `plextranslator capture --audio-format dshow --audio-device "audio=Stereo Mix"` |

Tuning: `--window-seconds` trades latency for accuracy (smaller = snappier but
choppier); `--overlap-seconds` keeps words from being clipped at window edges.

**Still hearing the audio (`--monitor-device`).** To capture system audio you
normally route playback to a loopback device (e.g. Stereo Mix), which can leave
you unable to hear it on your usual speakers. Rather than fight Windows' "Listen
to this device" routing, plextranslator can play the captured audio out to a
device of your choice:

```bash
pip install sounddevice
plextranslator capture --list-monitor-devices          # find your output, e.g. Samsung
plextranslator capture --source-language ja --model small `
  --audio-format dshow --audio-device "audio=Stereo Mix (Realtek High Definition Audio)" `
  --monitor-device "Samsung"
```

`--monitor-device` takes a device-name substring or index from
`--list-monitor-devices`. The passthrough is 16 kHz mono (fine for dialogue,
lower fidelity than the original) and runs a beat behind the video. If you can
get Windows' "Listen to this device" working, that's higher fidelity; this is the
fallback when audio routing won't cooperate.

Captions are **de-duplicated and smoothed**: because consecutive windows overlap,
their translations repeat boundary words (window A ends "…running away", window B
starts "away from us"). plextranslator merges windows into one continuous
transcript — stripping each new window's overlapping prefix — and shows the last
sentence or two, so captions read smoothly instead of stuttering. Pass
`--no-dedupe` to show each window verbatim (useful for debugging).

> Because each window is transcribed independently, capture mode is best on a
> faster model (`medium`/`large-v3` on a GPU). On CPU, try `--model small` and a
> larger `--window-seconds`.

### One-click Korean (best quality) — Windows

`scripts/watch-korean.ps1` wraps capture mode for Korean shows with the best
Whisper model (`large-v3`) and Claude (LLM) refinement, and opens the overlay for
you. Double-click `scripts/watch-korean.bat`, or run the `.ps1` from PowerShell.

One-time setup:
```powershell
pip install -e ".[run,llm,monitor]"
# put your Claude key in a .env file in the repo root:
#   ANTHROPIC_API_KEY=sk-ant-...
```
Then edit the `settings` block at the top of `watch-korean.ps1` (your capture
device name, and `--monitor-device` if you use it instead of Windows "Listen").

> ⚠️ **`large-v3` on a CPU (no GPU) is heavy** and may fall progressively behind
> on long shows; LLM refinement adds a second or two per line too. If captions
> lag, change `$Model` to `medium` (or `small`) in the script. The model affects
> quality and caption delay, not the audio you hear.

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

## Synology / NAS deployment

You can run plextranslator on the same box as Plex (Synology, etc.) via Docker.

> ⚠️ **CPU reality check (important).** Real-time translation needs a capable CPU
> (ideally a GPU). Low-power NAS CPUs — notably the **Intel Atom C2538 in the
> DS1517+**, which has **no AVX** — are a poor fit: the default faster-whisper
> backend often *requires* AVX and may fail with `Illegal instruction`. For those
> CPUs use the **whisper.cpp backend** (below), which runs without AVX. Even so,
> the NAS is best for **overnight `library` batch** with a small model, not
> real-time — for `live`/`web` use **Architecture A**.

### Transcription backends

| Backend | Set with | Runs on | Use it for |
|---------|----------|---------|------------|
| `faster-whisper` (default) | — | GPU, or AVX-capable CPU | Best quality/speed; real-time on a GPU. |
| `whisper.cpp` | `--backend whisper.cpp` | **Any CPU, incl. non-AVX (Atom)** | Making a low-power NAS viable. |

**Using the whisper.cpp backend** (e.g. on the DS1517+):

1. Get the `whisper-cli` binary — build [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
   with AVX disabled so it runs on the Atom, or use the Docker image below which
   does this for you:
   ```bash
   cmake -S whisper.cpp -B build -DGGML_NATIVE=OFF -DGGML_AVX=OFF -DGGML_AVX2=OFF -DGGML_FMA=OFF
   cmake --build build -j --config Release   # -> build/bin/whisper-cli
   ```
2. Download a ggml model (e.g. `ggml-small.bin`) from
   [Hugging Face](https://huggingface.co/ggerganov/whisper.cpp).
3. Run with the backend selected:
   ```bash
   plextranslator library \
     --backend whisper.cpp \
     --whisper-cpp-bin /usr/local/bin/whisper-cli \
     --whisper-cpp-model /models/ggml-small.bin \
     --whisper-cpp-threads 4
   ```

Install with the lighter extra that skips ctranslate2 entirely:
`pip install -e '.[whispercpp]'`.

### Architecture A — real-time on a stronger PC, Plex stays on the NAS

Run the app on a machine with AVX / a GPU that mounts your NAS media share. The
path Plex reports (`/volume1/video/...`) won't match your mount (`/mnt/plex`), so
use `--path-map` (or `PLEXTRANSLATOR_PATH_MAP`):

```bash
plextranslator live \
  --plex-url http://<nas-ip>:32400 --plex-token <token> \
  --path-map "/volume1/video=>/mnt/plex" \
  --model large-v3
```

### Architecture B — NAS-only overnight batch (Docker / Container Manager)

```bash
cp .env.example .env          # set PLEX_BASEURL + PLEX_TOKEN
# Edit docker-compose.yml: point the media volume at YOUR library path,
# mounted at the SAME path Plex uses (so sidecars land next to the media).

docker compose run --rm plextranslator config   # validate config
docker compose run --rm plextranslator doctor   # preflight: ffmpeg/backend/Plex
docker compose up plextranslator                 # runs `library` (batch)
```

On the **DS1517+ (no AVX)**, build the image with the whisper.cpp backend and
point it at a ggml model:

```bash
docker build --build-arg WITH_WHISPERCPP=1 --build-arg EXTRAS=whispercpp \
             -t plextranslator:nas .
# Put a ggml model in ./models (e.g. ggml-small.bin), then in .env set:
#   PLEXTRANSLATOR_BACKEND=whisper.cpp
#   PLEXTRANSLATOR_WHISPER_CPP_MODEL=/models/ggml-small.bin
docker compose run --rm plextranslator library
```

(The image's `whisper-cli` is compiled with AVX off, so it runs on the Atom even
if you build the image on a newer machine.)

The compose file mounts a `./models` volume so the Whisper model is downloaded
once and reused. Build with the LLM extra (`EXTRAS: run,llm`) to enable
`--use-llm`.

**Schedule it** (DSM **Control Panel → Task Scheduler → Create → Scheduled Task →
User-defined script**, run nightly):

```sh
# either the container form...
cd /volume1/docker/plextranslator && docker compose run --rm plextranslator library
# ...or the bundled helper (skips items that already have English subs):
/volume1/docker/plextranslator/scripts/translate_new.sh "Korean Films" "Japanese Films"
```

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
  capture.py       # live system-audio capture (Netflix & any streaming)
  dedupe.py        # overlap de-duplication / smoothing for rolling captions
  doctor.py        # `doctor` preflight checks (ffmpeg, backend, model, Plex)
  cli.py           # argparse entrypoint
Dockerfile          # container (ffmpeg + plextranslator)
docker-compose.yml  # Synology / Docker deployment (batch or web)
scripts/translate_new.sh  # nightly batch helper for Task Scheduler / cron
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
