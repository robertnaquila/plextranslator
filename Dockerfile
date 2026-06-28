# plextranslator container.
#
# NOTE on Synology DS1517+ (Intel Atom C2538): that CPU has no AVX, and
# faster-whisper's CTranslate2 backend often requires AVX (may fail with
# "Illegal instruction"). This image runs fine on AVX-capable x86-64 hosts.
# On the Atom NAS, expect it to be slow at best — use it for overnight `library`
# batch runs, not real-time, and see README "Synology / NAS deployment".

FROM python:3.11-slim

# ffmpeg is required for audio extraction / capture.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Persist downloaded Whisper models here (mount a volume at /models).
ENV HF_HOME=/models \
    XDG_CACHE_HOME=/models \
    PLEXTRANSLATOR_OUTPUT_DIR=/out \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY plextranslator ./plextranslator

# Install runtime deps. Add the llm extra for optional Claude refinement.
ARG EXTRAS=run
RUN pip install --no-cache-dir ".[${EXTRAS}]"

# Default to validating config; compose overrides the command (e.g. library/web).
ENTRYPOINT ["plextranslator"]
CMD ["config"]
