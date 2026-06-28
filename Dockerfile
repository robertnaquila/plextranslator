# plextranslator container.
#
# Two backends:
#   - faster-whisper (default): needs an AVX-capable CPU or a GPU.
#   - whisper.cpp (WITH_WHISPERCPP=1): runs on non-AVX CPUs like the Synology
#     DS1517+ (Atom C2538). Built below with AVX/AVX2/FMA disabled so the binary
#     is portable to such CPUs regardless of the build host. This is the option
#     that makes the NAS viable; pair it with EXTRAS=whispercpp.
#
# Examples:
#   docker build -t plextranslator .                                  # faster-whisper
#   docker build --build-arg WITH_WHISPERCPP=1 --build-arg EXTRAS=whispercpp \
#                -t plextranslator:nas .                              # NAS / no-AVX

FROM python:3.11-slim

# ffmpeg is required for audio extraction / capture.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Optionally build whisper.cpp with AVX disabled (portable to non-AVX CPUs).
ARG WITH_WHISPERCPP=0
ARG WHISPERCPP_REF=master
RUN if [ "$WITH_WHISPERCPP" = "1" ]; then \
      set -eux; \
      apt-get update; \
      apt-get install -y --no-install-recommends git build-essential cmake ca-certificates; \
      git clone --depth 1 --branch "$WHISPERCPP_REF" https://github.com/ggml-org/whisper.cpp /tmp/whisper.cpp; \
      cmake -S /tmp/whisper.cpp -B /tmp/whisper.cpp/build \
            -DCMAKE_BUILD_TYPE=Release \
            -DGGML_NATIVE=OFF -DGGML_AVX=OFF -DGGML_AVX2=OFF -DGGML_FMA=OFF -DGGML_F16C=OFF; \
      cmake --build /tmp/whisper.cpp/build -j --config Release; \
      cp /tmp/whisper.cpp/build/bin/whisper-cli /usr/local/bin/whisper-cli; \
      apt-get purge -y git build-essential cmake; apt-get autoremove -y; \
      rm -rf /tmp/whisper.cpp /var/lib/apt/lists/*; \
    fi

# Persist downloaded Whisper models here (mount a volume at /models).
ENV HF_HOME=/models \
    XDG_CACHE_HOME=/models \
    PLEXTRANSLATOR_OUTPUT_DIR=/out \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY plextranslator ./plextranslator

# Install Python deps. Use EXTRAS=run for faster-whisper, or EXTRAS=whispercpp
# (no ctranslate2/AVX wheel) when running the whisper.cpp backend on a NAS.
ARG EXTRAS=run
RUN pip install --no-cache-dir ".[${EXTRAS}]"

# Default to validating config; compose overrides the command (e.g. library/web).
ENTRYPOINT ["plextranslator"]
CMD ["config"]
