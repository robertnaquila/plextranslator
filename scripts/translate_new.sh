#!/bin/sh
# Batch-translate new Korean/Japanese items, intended for a nightly schedule
# (Synology DSM Task Scheduler or cron). `library` already skips items that
# already have English subtitles, so re-running only processes new media.
#
# Usage:
#   scripts/translate_new.sh                 # all KO/JA sections
#   scripts/translate_new.sh "Korean Films"  # one section (repeat flag for more)
#
# It loads ./.env (PLEX_BASEURL, PLEX_TOKEN, model, etc.) if present, then runs
# the library command. Point DSM Task Scheduler at this script, or wrap the
# docker compose form shown in the comments below.

set -eu

cd "$(dirname "$0")/.."

# Load .env if present.
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

SECTION_ARGS=""
for s in "$@"; do
  SECTION_ARGS="$SECTION_ARGS --section \"$s\""
done

# --- Bare-metal / venv form ------------------------------------------------
# Requires `pip install -e '.[run]'` in the active environment.
eval "plextranslator library $SECTION_ARGS"

# --- Docker form (uncomment to use the container instead) ------------------
# eval "docker compose run --rm plextranslator library $SECTION_ARGS"
