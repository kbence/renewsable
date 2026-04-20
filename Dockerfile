# renewsable dev-only image.
#
# Purpose: exercise the full build pipeline (RSS fetch → goosepaper →
# WeasyPrint PDF) in a Linux sandbox that mirrors Raspberry Pi OS
# Bookworm, without installing Pango / Cairo / Harfbuzz on a macOS host.
#
# NOT for production: the Pi deployment still uses scripts/install-pi.sh
# and a systemd user timer on bare-metal Bookworm. This image deliberately
# omits rmapi and systemd — it is a local developer tool.
#
# Usage:
#   docker build -t renewsable:dev .
#   docker run --rm \
#     -v "$PWD/config:/config:ro" \
#     -v "$HOME/.local/state/renewsable:/state" \
#     renewsable:dev \
#     --config /config/config.example.json build
#
# The produced PDF lands in /state/out/ on the host via the bind-mount.

FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XDG_STATE_HOME=/state \
    XDG_CONFIG_HOME=/config

# System deps: matches scripts/install-pi.sh minus the Pi-specific pieces
# (rmapi, systemd). `git` is required to install goosepaper from its tag.
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 \
      python3-dev \
      python3-pip \
      python3-venv \
      python3-cffi \
      python3-brotli \
      libpango-1.0-0 \
      libpangoft2-1.0-0 \
      libharfbuzz0b \
      libffi-dev \
      shared-mime-info \
      fonts-dejavu \
      fonts-noto-core \
      git \
      ca-certificates \
      curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dep manifest first so the dep layer is cached across code edits.
COPY pyproject.toml README.md ./
COPY src ./src

# Bookworm ships PEP 668 — use a venv rather than --break-system-packages,
# and put it on PATH so `renewsable` and `goosepaper` are callable directly.
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -e .
ENV PATH="/opt/venv/bin:${PATH}"

# Sanity-check at build time so a broken image fails fast.
RUN renewsable --help > /dev/null && goosepaper --help > /dev/null

# /config is read-only mount target for the user's config.json(s).
# /state is the XDG state dir (output PDFs + log files land here).
VOLUME ["/config", "/state"]

ENTRYPOINT ["renewsable"]
CMD ["--help"]
