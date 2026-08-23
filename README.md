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
- **Two audio-free live modes** need no Plex and no media files: `capture`
  transcribes the system audio you're playing (Netflix etc.), and `ocr` reads
  **burned-in subtitles straight off the screen** and translates them — the
  lowest-latency option when the show already shows original-language subs.

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

**Staying seamless.** Three things keep capture-mode captions flowing:

- **Catch-up** (on by default): if transcription runs slower than real time (big
  model on CPU), stale audio is skipped after each window so caption lag stays
  bounded at roughly one window + inference time instead of growing all session.
  The console logs `skipping stale audio to catch up` when it kicks in; disable
  with `--no-catch-up` if you'd rather transcribe everything late.
- **Async Claude refinement**: with `--use-llm`, the raw translation is shown
  immediately and Claude's polished wording swaps in a moment later — refinement
  never delays the first caption.
- **Adaptive hold**: each caption stays on screen until the next one lands
  (sized to the observed transcription time), so slow models no longer blink to
  blank between windows.
- `--beam-size 1` makes Whisper ~2x faster for a small accuracy cost — a good
  lever on CPU.

### OCR — burned-in subtitles read off the screen (lowest latency)

If the show already has **burned-in subtitles in the original language** — most
Korean variety shows, hardsubbed rips, or shows you stream from someone else's
server — you don't need audio transcription at all. `ocr` watches the part of
the screen where those subtitles appear, reads each new line the moment it shows
up, and translates it with Claude:

```bash
pip install -e ".[ocr]"
pip install winsdk                       # Windows built-in OCR (recommended)
plextranslator ocr --source-language ko  # watches the bottom 30% of the screen
```

Then open http://127.0.0.1:8765/ for the captions — and **keep that overlay
window outside the watched region** (above the video or on another monitor), or
the OCR would read its own captions. A script filter (Hangul/kana required for
ko/ja) guards against that too.

This is the snappiest mode: captions typically land **1–2 s after the original
line appears** (vs. 8 s+ for audio windows), there's **no Whisper model, no
audio routing, and almost no CPU load**. Whisper/ffmpeg aren't involved at all.

- **Pick the region**: `--region bottom` (default, bottom 30%), `--region
  bottom:40`, exact pixels `--region "0,780,1920,300"`, or drag it on screen
  with `--select-region` (prints the coordinates for reuse).
- **Test it**: `plextranslator ocr --probe` grabs the region once and prints
  what it read — run it while a subtitle is on screen. Add
  `--save-frame frame.png` to also write the captured region *and* the
  preprocessed black-and-white image the OCR engine actually sees; opening
  those two files tells you instantly whether the region is wrong or the
  contrast is.
- **If the probe returns gibberish**, the region is almost always too wide —
  the full-width default includes video imagery and UI that Tesseract tries to
  read as text. Narrow it to just the subtitle band with `--select-region`.
  On a tight single-line strip, `--psm 7` beats the default `--psm 6`.
- **OCR engines** (`--ocr-backend`): `windows` uses the OCR built into
  Windows 10/11 (`pip install winsdk`, plus the language pack: Settings → Time &
  Language → Language & region → Add a language → 한국어); `tesseract` works
  everywhere (`pip install pytesseract` + the Tesseract binary with e.g. `kor`
  data); `auto` (default) tries Windows first, then Tesseract.
- **Latency knobs**: `--interval` (default 0.4 s between screen checks) and
  `--stable-frames` (default 2 frames of debounce; `1` is fastest but may
  flicker on OCR misreads). Repeated lines are cached and translate instantly.
- Translation uses Claude (`ANTHROPIC_API_KEY`); a fast model like Claude Haiku
  keeps per-line latency well under a second. Without a key it shows the
  original text untranslated.

### One-click Korean — Windows

Two launchers in `scripts/`, both double-clickable:

- **`watch-korean-ocr.bat`** — for shows with burned-in Korean subs (the fastest
  mode; no Whisper or audio setup). Watches the bottom of the screen, translates
  with Claude Haiku by default.
- **`watch-korean.bat`** — audio capture + Whisper (`small` by default, the CPU
  sweet spot) + Claude refinement, for content with no burned-in subs.

One-time setup:
```powershell
pip install -e ".[run,llm,monitor,ocr]"
# put your Claude key in a .env file in the repo root:
#   ANTHROPIC_API_KEY=sk-ant-...
```
A `.env` in the working directory is loaded automatically by every command
(real environment variables still take precedence).
Then edit the `settings` block at the top of either `.ps1` (capture device /
screen region, model choice). For OCR, find your subtitle region once with
`python -m plextranslator ocr --select-region` and paste the printed
coordinates into `$Region` in `watch-korean-ocr.ps1` — a tight region around
just the subtitle text is both faster and much more accurate than the
`bottom`/`bottom:N` default.

**Desktop shortcut**: double-click `scripts\create-desktop-shortcut.bat` once
to add a "Korean OCR Subtitles" icon to your Desktop — double-click that icon
any time instead of opening this folder. (If PowerShell's default execution
policy blocks running `.ps1` files directly, this `.bat` sidesteps it; running
`scripts\create-desktop-shortcut.ps1` from PowerShell works too once you allow
scripts with e.g. `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.)

> ⚠️ **Model reality check for audio capture on CPU**: `small` keeps up with
> real time; `medium` is borderline; `large-v3` needs a GPU (with catch-up it
> stays bounded but runs far behind). The model affects caption quality and
> delay, not the audio you hear.

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
  ocr.py           # screen-OCR mode: burned-in subs -> Claude translation
  ocr_picker.py    # drag-to-select region picker for `ocr --select-region`
  dedupe.py        # overlap de-duplication / smoothing for rolling captions
  workers.py       # latest-only background worker (refinement/translation)
  doctor.py        # `doctor` preflight checks (ffmpeg, backend, model, Plex)
  cli.py           # argparse entrypoint
Dockerfile          # container (ffmpeg + plextranslator)
docker-compose.yml  # Synology / Docker deployment (batch or web)
scripts/translate_new.sh  # nightly batch helper for Task Scheduler / cron
scripts/watch-korean*.bat # one-click Windows launchers (audio / screen-OCR)
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
