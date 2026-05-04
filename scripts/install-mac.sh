#!/usr/bin/env bash
# install-mac.sh — macOS workstation bootstrap for renewsable (manual mode).
#
# Run from the project root on a Mac (Apple Silicon or Intel):
#     ./scripts/install-mac.sh
#
# What it does (idempotent):
#   1. Sanity-checks the working directory and the host platform.
#   2. Resolves the host architecture (arm64 / x86_64) to the matching
#      upstream ddvk/rmapi macOS asset name.
#   3. Verifies python3 >= 3.11 is on PATH (no Homebrew install attempted —
#      the script never mutates system state).
#   4. Creates a project-local venv at .venv and installs renewsable[dev]
#      in editable mode.
#   5. Downloads the LATEST ddvk/rmapi macOS release matching the host arch
#      (no version pin and no SHA-256 verification — see "Why no pin" below)
#      and installs the rmapi binary into .venv/bin/.
#   6. Defensively clears the com.apple.quarantine xattr on the rmapi binary
#      so Gatekeeper does not block it on first launch.
#   7. Smoke-tests the installation and prints the manual next-steps banner.
#
# Requirements covered: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6.
#
# Why no pin?
#   The Pi-side install-pi.sh pins rmapi to v0.0.32 with an SHA-256, and that
#   pinned binary is currently broken against the live reMarkable cloud
#   (sync-v3 invalid hash; fixed upstream in v0.0.33). For a single-operator
#   project the failure mode "stuck on a broken pinned binary" has historically
#   been higher-impact than "upstream release tampered with", so this script
#   tracks the latest release and trusts the upstream. See research.md and the
#   design's Boundary Commitments for the full tradeoff write-up. The Pi-side
#   pin policy is intentionally untouched by this spec.
#
# Why no Homebrew / sudo / apt?
#   This script must not mutate system state outside the project's .venv/ and
#   a mktemp temp directory. Required tools (python3, curl, unzip, install,
#   mktemp, xattr, uname) all ship with macOS by default; Python 3.11+ is the
#   only thing the operator may have to install themselves, and the script
#   tells them to do that via Homebrew rather than doing it for them.
#
# Bash 3.2 note: macOS ships bash 3.2. This script avoids bash-4-only features
# (associative arrays, ${var^^}, mapfile, etc.) — same constraint that
# install-pi.sh follows in spirit.

set -euo pipefail

# --- Paths and constants -----------------------------------------------------
VENV_DIR=".venv"
# rmapi release is fetched via GitHub's stable releases/latest/download URL
# pattern — this resolves at request time to the current latest release asset.
RMAPI_BASE_URL="https://github.com/ddvk/rmapi/releases/latest/download"

# --- Helpers -----------------------------------------------------------------
# Same prefixed/coloured log helpers as install-pi.sh. Stdout for log, stderr
# for warn/die so a caller can tee one and let the other through unmixed.
log()  { printf '\033[1;34m[install-mac]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install-mac warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[install-mac error]\033[0m %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found on PATH: $1"
}

# --- 1. Sanity: must run from the project root -------------------------------
# Mirrors install-pi.sh: refuse early if invoked from the wrong cwd. This also
# protects step 4's `pip install -e ".[dev]"` from running against the wrong
# pyproject.toml.
if [ ! -f "pyproject.toml" ] || [ ! -d "src/renewsable" ]; then
  die "run this script from the project root (where pyproject.toml lives)"
fi

# --- 2. Sanity: must be Darwin (macOS) ---------------------------------------
# We refuse non-Darwin hosts BEFORE doing anything that could mutate the
# working directory. The Pi script is the right tool for Linux; pointing the
# operator there is more useful than a generic "wrong platform" message.
KERNEL=$(uname -s)
if [ "$KERNEL" != "Darwin" ]; then
  die "this script targets macOS (Darwin); detected kernel: $KERNEL. \
Only macOS is supported by install-mac.sh — use scripts/install-pi.sh on \
Raspberry Pi OS, and consult the project README for other platforms."
fi

# --- 3. Architecture resolution ----------------------------------------------
# Single source of truth for upstream asset names. If ddvk/rmapi ever renames
# its macOS release assets, this case block is the only place the script
# needs to be updated.
#   arm64  → Apple Silicon (M1/M2/M3/M4)        → rmapi-macos-arm64.zip
#   x86_64 → Intel                              → rmapi-macos-intel.zip
# Anything else on Darwin (e.g. an unexpected uname report) is rejected with
# a guidance message that names the detected arch.
ARCH=$(uname -m)
case "$ARCH" in
  arm64)
    RMAPI_ZIP="rmapi-macos-arm64.zip" ;;
  x86_64)
    RMAPI_ZIP="rmapi-macos-intel.zip" ;;
  *)
    die "unsupported macOS architecture: $ARCH (expected arm64 or x86_64)" ;;
esac
RMAPI_URL="${RMAPI_BASE_URL}/${RMAPI_ZIP}"
log "platform: Darwin / $ARCH (asset: $RMAPI_ZIP)"

# --- 4. Required commands ----------------------------------------------------
# All of these ship with macOS by default. We do not require sudo / brew /
# apt-get — this script must not mutate system state outside .venv/ and a
# mktemp temp directory.
require_cmd python3
require_cmd curl
require_cmd unzip
require_cmd install
require_cmd mktemp
require_cmd xattr
require_cmd uname

# --- 5. Python version check (>= 3.11) ---------------------------------------
# pyproject.toml's requires-python floor is 3.11. We do NOT attempt to install
# Python — if the operator is on an older interpreter, we point them at
# Homebrew and exit. The check uses python3 itself so that the comparison
# never disagrees with what pip will subsequently see.
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  PY_VER=$(python3 --version 2>&1 || true)
  die "python3 >= 3.11 is required; detected: $PY_VER. \
Install a newer interpreter (e.g. \`brew install python@3.12\`) and re-run \
this script. This script will not install Python for you."
fi
log "python3 version check passed ($(python3 --version 2>&1))"

# --- 6. Create / reuse the venv ----------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
  log "creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
else
  log "venv already exists at $VENV_DIR (reusing)"
fi

# Use the venv interpreter and pip directly — no need to `source activate`
# from inside a script.
VENV_PIP="$VENV_DIR/bin/pip"
VENV_PY="$VENV_DIR/bin/python"
[ -x "$VENV_PY" ] || die "venv python not found at $VENV_PY"

# --- 7. Editable install with dev extras -------------------------------------
log "upgrading pip inside the venv"
"$VENV_PIP" install --upgrade pip

log "installing renewsable in editable mode (with dev extras)"
"$VENV_PIP" install -e ".[dev]"

# --- 8. Download and install rmapi (idempotent) ------------------------------
# If the binary is already in place from a previous run, skip the download
# and extract entirely. We still re-run the quarantine clear in this branch:
# it is cheap, idempotent, and protects against the (rare) case where the
# binary was placed by a quarantine-setting tool between runs.
RMAPI_BIN="$VENV_DIR/bin/rmapi"

if [ -x "$RMAPI_BIN" ]; then
  log "rmapi already installed at $RMAPI_BIN — skipping download"
  # Defensive quarantine clear. See step 10 below for the rationale; running
  # it here is a no-op when the xattr is absent (`|| true`).
  xattr -d com.apple.quarantine "$RMAPI_BIN" 2>/dev/null || true
else
  log "downloading rmapi from $RMAPI_URL"
  TMP=$(mktemp -d)
  # Always clean up the temp dir, including on failure paths under set -e.
  trap 'rm -rf "$TMP"' EXIT

  # --fail   → non-2xx HTTP makes curl exit non-zero (bubbled up by set -e).
  # --silent → suppress progress meter (we already announced via `log`).
  # --show-error → keep real error messages on stderr even with --silent.
  # --location  → follow GitHub's redirect from /releases/latest/download/...
  # NB: deliberately no -o without --create-dirs etc.; the temp dir already
  # exists.
  if ! curl --fail --silent --show-error --location \
      --output "$TMP/$RMAPI_ZIP" "$RMAPI_URL"; then
    die "failed to download rmapi from $RMAPI_URL — \
check network connectivity and that the upstream asset still exists"
  fi

  # --- 9. Extract --------------------------------------------------------
  # macOS rmapi releases ship a .zip (not a tar.gz like the Pi-side asset),
  # so we use unzip rather than tar -xzf. -q keeps stdout tidy — extraction
  # errors still surface via the non-zero exit and the wrapping die().
  if ! unzip -q "$TMP/$RMAPI_ZIP" -d "$TMP"; then
    die "failed to extract $RMAPI_ZIP into $TMP — the archive may be \
truncated or corrupt; re-run this script to retry the download"
  fi
  if [ ! -f "$TMP/rmapi" ]; then
    die "rmapi binary not found inside $RMAPI_ZIP after extraction to $TMP — \
release layout may have changed; check $RMAPI_BASE_URL"
  fi

  # --- 10. Install rmapi to venv bin -------------------------------------
  # `install` sets the mode atomically. The venv bin/ directory was created
  # by `python3 -m venv` above, so we do not need -D to create parents.
  install -m 0755 "$TMP/rmapi" "$RMAPI_BIN"
  log "rmapi installed at $RMAPI_BIN"

  # --- 11. Quarantine clear ----------------------------------------------
  # rmapi is unsigned/unnotarized for macOS. curl typically does NOT set the
  # com.apple.quarantine xattr, so this is usually a no-op — but we run it
  # defensively. If the binary is ever sourced via a browser download or
  # another quarantine-setting path, this step prevents Gatekeeper from
  # rejecting the first invocation (which would surface as a confusing
  # failure during `renewsable pair`). `|| true` keeps the script idempotent
  # when the xattr is absent (the common case).
  xattr -d com.apple.quarantine "$RMAPI_BIN" 2>/dev/null || true
fi

# --- 12. Smoke test ----------------------------------------------------------
# `renewsable --help` is a hard requirement: if the editable install is
# broken, we want the script to fail here rather than silently producing
# a venv that does not work. `rmapi version` is best-effort — different
# rmapi releases shape the `version` subcommand slightly differently and a
# Gatekeeper rejection would surface here as a non-zero exit; downgrading
# to a warn keeps the script useful and pushes any real problem to the
# subsequent `renewsable pair` step. (Mirrors install-pi.sh.)
log "verifying installation"
"$VENV_DIR/bin/renewsable" --help >/dev/null \
  || die "renewsable --help failed inside the venv"
"$RMAPI_BIN" version >/dev/null 2>&1 \
  || warn "rmapi did not respond to \`version\` — continuing, pairing will surface real problems"
log "renewsable + rmapi installed successfully"

# --- 13. Next-steps banner ---------------------------------------------------
# Keep this terse; the README "Setup on macOS (manual mode)" section carries
# the full runbook. The key callout is that this is a MANUAL mode — there is
# no scheduler equivalent, and `install-schedule` will exit non-zero by
# design.
printf '\n\033[1;32mInstall complete.\033[0m  Next steps (run in order):\n\n'
cat <<EOF
  1. Pair this Mac with your reMarkable account (interactive, one-time):

         source $VENV_DIR/bin/activate
         renewsable pair

     You will be prompted for the 8-character code from
     https://my.remarkable.com/device/desktop/connect.

  2. Verify the full pipeline end-to-end against the example config:

         renewsable --config config/config.example.json test-pipeline

     This builds a dated EPUB and uploads it to the configured reMarkable
     folder without waiting for any scheduled fire time.

  3. For daily use, run the build manually whenever you want a fresh digest:

         renewsable --config config/config.example.json run

  4. (Optional) Copy config/config.example.json to
     ~/.config/renewsable/config.json and edit feeds to taste.

  Note: scheduling is NOT supported on macOS — \`renewsable install-schedule\`
  exits non-zero on Darwin by design. There is no launchd / cron integration;
  you invoke \`renewsable run\` yourself when you want a digest.
EOF
