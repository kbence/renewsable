#!/usr/bin/env bash
# install-pi.sh — Raspberry Pi OS Bookworm 64-bit bootstrap for renewsable.
#
# Run from the project root on the Pi:
#     ./scripts/install-pi.sh
#
# What it does (idempotent):
#   1. Sanity-checks the working directory and the host architecture.
#   2. Installs apt prerequisites: a baseline of system libraries plus
#      Hungarian/Latin fonts (mostly inherited from the goosepaper/WeasyPrint
#      era; harmless to keep now that output is EPUB).
#   3. Creates a project-local venv at .venv and installs renewsable[dev]
#      in editable mode.
#   4. Downloads the pinned ddvk/rmapi linux-arm64 release tarball, verifies
#      its SHA-256 against an embedded pin, and installs the rmapi binary
#      into .venv/bin/.
#   5. Prints the manual next steps (pair, test-pipeline, install-schedule,
#      enable-linger).
#
# Requirements covered: 6.1, 7.1, 7.2, 7.3.

set -euo pipefail

# --- Pinned versions ---------------------------------------------------------
# rmapi release to install. Bump here when updating; also refresh the SHA-256.
RMAPI_VERSION="v0.0.32"
RMAPI_TAR="rmapi-linux-arm64.tar.gz"
RMAPI_URL="https://github.com/ddvk/rmapi/releases/download/${RMAPI_VERSION}/${RMAPI_TAR}"
# SHA-256 of ${RMAPI_TAR} as published for ${RMAPI_VERSION}. Verified
# 2026-04-19 by downloading the release asset from github.com/ddvk/rmapi.
RMAPI_SHA256="6e5ced303da31989786c5bf6abd933202c046576722a3fe0d89e2fa50e0ea102"

VENV_DIR=".venv"

# --- Helpers -----------------------------------------------------------------
log()  { printf '\033[1;34m[install-pi]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install-pi warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[install-pi error]\033[0m %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found on PATH: $1"
}

# --- 1. Sanity: must run from the project root -------------------------------
if [ ! -f "pyproject.toml" ] || [ ! -d "src/renewsable" ]; then
  die "run this script from the project root (where pyproject.toml lives)"
fi

# --- 2. Sanity: must be Linux on aarch64 (Raspberry Pi OS 64-bit) ------------
# The ddvk/rmapi project only ships linux-amd64 and linux-arm64 binaries.
# Raspberry Pi OS reports the 64-bit kernel as `aarch64`; the 32-bit image
# reports `armv7l` / `armhf` and is explicitly unsupported by this script.
# On a macOS dev box `uname -s` is `Darwin`, so the kernel check fails first
# and the user is stopped before any apt/pip damage can happen.
KERNEL=$(uname -s)
ARCH=$(uname -m)
if [ "$KERNEL" != "Linux" ]; then
  die "this script targets Raspberry Pi OS (Linux); detected kernel: $KERNEL. \
Do not run it on the macOS dev box — deploy to the Pi and run it there."
fi
case "$ARCH" in
  aarch64|arm64)
    : ;;
  armv6l|armv7l|armhf)
    die "32-bit Raspberry Pi OS ($ARCH) is not supported. Reflash the SD card \
with the 64-bit Raspberry Pi OS Bookworm image and re-run this script." ;;
  *)
    die "unsupported architecture: $ARCH (need aarch64 / arm64)" ;;
esac
log "architecture: Linux / $ARCH (ok)"

# --- 3. Prereq commands ------------------------------------------------------
require_cmd sudo
require_cmd apt-get
require_cmd python3
require_cmd curl
require_cmd tar
require_cmd install
# sha256sum lives in coreutils on Raspberry Pi OS and is used for the
# rmapi release checksum verification below.
require_cmd sha256sum

# --- 4. apt install system dependencies (idempotent) -------------------------
# Baseline set originally chosen for the goosepaper/WeasyPrint era: Pango,
# HarfBuzz, libffi, and helper Python bindings supported PDF rendering.
# Output is now EPUB (assembled in-process via ebooklib) and the reMarkable
# reader picks fonts itself, so most of these are no longer load-bearing
# for renewsable's runtime, but kept as a harmless baseline. python3-cffi
# and python3-brotli remain useful for the lxml/trafilatura stack.
# fonts-dejavu + fonts-noto-core stay because Hungarian-supporting fonts
# are cheap to keep and useful system-wide.
APT_PACKAGES=(
  python3-dev
  python3-pip
  python3-venv
  python3-cffi
  python3-brotli
  libpango-1.0-0
  libpangoft2-1.0-0
  libharfbuzz0b
  libffi-dev
  shared-mime-info
  fonts-dejavu
  fonts-noto-core
)
log "installing apt prerequisites: ${APT_PACKAGES[*]}"
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends "${APT_PACKAGES[@]}"

# --- 5. Create / refresh the venv --------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
  log "creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
else
  log "venv already exists at $VENV_DIR (reusing)"
fi

# Use the venv interpreter directly — no need to `source activate` in a script.
VENV_PIP="$VENV_DIR/bin/pip"
VENV_PY="$VENV_DIR/bin/python"
[ -x "$VENV_PY" ] || die "venv python not found at $VENV_PY"

# --- 6. Install renewsable in editable mode + dev deps -----------------------
log "upgrading pip inside the venv"
"$VENV_PIP" install --upgrade pip

log "installing renewsable in editable mode (with dev extras)"
"$VENV_PIP" install -e ".[dev]"

# --- 7. Download and install rmapi (ddvk fork) -------------------------------
RMAPI_BIN="$VENV_DIR/bin/rmapi"

if [ -x "$RMAPI_BIN" ]; then
  log "rmapi already installed at $RMAPI_BIN — skipping download"
else
  log "downloading rmapi $RMAPI_VERSION from ${RMAPI_URL}"
  TMP=$(mktemp -d)
  trap 'rm -rf "$TMP"' EXIT

  curl --fail --silent --show-error --location \
    --output "$TMP/$RMAPI_TAR" "$RMAPI_URL"

  log "verifying SHA-256 checksum of $RMAPI_TAR"
  # sha256sum -c expects "<hash>  <filename>" and runs from cwd, so we cd
  # into the temp dir first. Any mismatch aborts the script.
  (
    cd "$TMP"
    printf '%s  %s\n' "$RMAPI_SHA256" "$RMAPI_TAR" | sha256sum --check --status
  ) || die "SHA-256 mismatch for $RMAPI_TAR — refusing to install. Expected \
$RMAPI_SHA256. The release may have been re-cut or tampered with; verify and \
update RMAPI_SHA256 in this script before retrying."

  tar -xzf "$TMP/$RMAPI_TAR" -C "$TMP"
  if [ ! -f "$TMP/rmapi" ]; then
    die "rmapi binary not found inside tarball; release layout may have changed"
  fi

  # `install` sets the mode atomically (-D would also create parents; the
  # venv bin/ directory already exists, so we only need the file itself).
  install -m 0755 "$TMP/rmapi" "$RMAPI_BIN"
  log "rmapi installed at $RMAPI_BIN"
fi

# --- 8. Smoke-test the install -----------------------------------------------
log "verifying installation"
"$VENV_DIR/bin/renewsable" --help >/dev/null \
  || die "renewsable --help failed inside the venv"
# rmapi's `version` subcommand exists on v0.0.32, but to stay tolerant of
# future CLI tweaks we accept any exit status here as long as the binary
# is executable and responds on stdout or stderr.
"$RMAPI_BIN" version >/dev/null 2>&1 \
  || warn "rmapi did not respond to \`version\` — continuing, pairing will surface real problems"
log "renewsable + rmapi installed successfully"

# --- 9. Next-steps banner ----------------------------------------------------
# Keep this block terse; the README carries the detailed runbook.
printf '\n\033[1;32mInstall complete.\033[0m  Next steps (run in order):\n\n'
cat <<EOF
  1. Pair this device with your reMarkable account (interactive, one-time):

         source $VENV_DIR/bin/activate
         renewsable pair

     You will be prompted for the 8-character code from
     https://my.remarkable.com/device/desktop/connect.

  2. Verify the full pipeline end-to-end against the example config:

         renewsable --config config/config.example.json test-pipeline

     This builds a dated PDF and uploads it to the configured reMarkable
     folder without waiting for the scheduled fire time.

  3. Install the daily systemd user timer (fires at schedule_time from the
     config — 05:30 local in the example):

         renewsable --config config/config.example.json install-schedule

  4. Allow the timer to fire even when you are not logged in over SSH:

         sudo loginctl enable-linger \$USER

  5. (Optional) Copy config/config.example.json to
     ~/.config/renewsable/config.json and edit feeds / schedule_time to
     taste. Re-run \`renewsable install-schedule\` after schedule changes.
EOF
