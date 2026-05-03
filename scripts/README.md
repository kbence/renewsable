# scripts/

Operational scripts for renewsable. These are meant to be run on the
Raspberry Pi — not on the macOS dev box.

## `install-pi.sh`

First-run bootstrap for a fresh Raspberry Pi OS Bookworm **64-bit** install.
Idempotent: re-running it is safe.

### What it does

1. Refuses to run anywhere other than Linux on `aarch64` / `arm64` (including
   an explicit, loud failure on macOS and on the 32-bit `armhf` Pi images).
2. `apt install`s a baseline set of system libraries plus DejaVu + Noto Core
   fonts. Some of these (Pango, HarfBuzz) were originally required for
   WeasyPrint-based PDF rendering in the goosepaper era; they are kept as a
   harmless baseline now that output is EPUB and the reMarkable reader picks
   fonts. `python3-cffi` and `python3-brotli` remain useful for the
   `lxml` / `trafilatura` stack:

   ```
   python3-dev python3-pip python3-venv python3-cffi python3-brotli
   libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi-dev shared-mime-info
   fonts-dejavu fonts-noto-core
   ```

3. Creates a project-local venv at `.venv/` and installs the package in
   editable mode with the `dev` extras (`pip install -e ".[dev]"`).
4. Downloads the pinned `rmapi-linux-arm64` release tarball from
   `github.com/ddvk/rmapi`, verifies its SHA-256 against an embedded pin,
   and installs the resulting `rmapi` binary into `.venv/bin/`.
5. Smoke-tests the install (`renewsable --help`, `rmapi version`).
6. Prints the next-steps runbook.

### Prerequisites

- Raspberry Pi OS **Bookworm 64-bit** (32-bit / armhf is not supported —
  `rmapi` has no prebuilt armhf binary, and the script refuses to run).
- A user account with `sudo` that can run `apt-get`.
- Outbound internet for `apt`, PyPI, and `github.com/ddvk/rmapi`.
- `bash`, `curl`, `tar`, `python3`, and `sha256sum` (all present on a
  default Bookworm install).

### Usage

From the project root on the Pi:

```sh
./scripts/install-pi.sh
```

Re-run it whenever you want to refresh the venv or pull in a new apt
package list; already-installed components are skipped.

### What it does NOT do

- Provision the Pi itself (OS install, SSH, Wi-Fi, hostname, timezone).
- Pair `rmapi` with your reMarkable account — run `renewsable pair`
  afterwards. Pairing requires the one-time 8-character code from
  `my.remarkable.com/device/desktop/connect` and must happen interactively.
- Install the systemd user timer — run
  `renewsable --config <path> install-schedule` afterwards.
- Enable `loginctl` lingering so the timer fires when you are not logged
  in — the script only *prints* the command; run
  `sudo loginctl enable-linger $USER` yourself.
- Configure `~/.config/renewsable/config.json`. Copy
  `config/config.example.json` to that path and edit it, or pass
  `--config <path>` to every invocation.

### Updating the pinned rmapi version

The rmapi version and SHA-256 are pinned at the top of `install-pi.sh`:

```sh
RMAPI_VERSION="v0.0.32"
RMAPI_SHA256="6e5ced303da31989786c5bf6abd933202c046576722a3fe0d89e2fa50e0ea102"
```

To bump:

1. Pick a new tag from `https://github.com/ddvk/rmapi/releases`.
2. Download the new `rmapi-linux-arm64.tar.gz` and run
   `sha256sum rmapi-linux-arm64.tar.gz` to get the hash.
3. Update both variables in the script.
4. Commit the change with a note about which release was verified and when.

### Local validation

On the dev box we can at least syntax-check the script and confirm the
arch gate fires. The full install is only exercised on a real Pi.

```sh
bash -n scripts/install-pi.sh                       # syntax check
./scripts/install-pi.sh                             # on macOS: must exit 1
command -v shellcheck && shellcheck scripts/install-pi.sh   # if installed
```

## `install-mac.sh` — rmapi fetch policy

The macOS bootstrap (`scripts/install-mac.sh`) takes a deliberately
different approach to obtaining `rmapi` than the Pi script does. It
downloads the **latest** upstream release via the GitHub redirect URL:

```
https://github.com/ddvk/rmapi/releases/latest/download/<asset>
```

The asset is selected from `uname -m`:

| `uname -m` | Asset                    | Host             |
|------------|--------------------------|------------------|
| `arm64`    | `rmapi-macos-arm64.zip`  | Apple Silicon    |
| `x86_64`   | `rmapi-macos-intel.zip`  | Intel Mac        |

There is **no version pin and no SHA-256 pin** on the macOS path. After
extracting the binary into `.venv/bin/rmapi`, the script defensively runs
`xattr -d com.apple.quarantine` on it (suppressing failure when the
attribute is absent) so an unsigned/unnotarized `rmapi` is not blocked by
Gatekeeper at first run. If a maintainer ever changes the install path or
swaps `curl` for another download tool, that quarantine-clear step must
continue to run.

**Rationale.** The Pi-side `v0.0.32` pin is broken in production today —
it triggers the sync-v3 `invalid hash` failure against the live
reMarkable cloud, which upstream fixed in `v0.0.33`. For a one-operator
project, getting stuck on a known-bad pinned binary has historically been
a higher-impact failure mode than a tampered upstream release. Trading
tamper detection (low-impact for a single operator pulling over HTTPS
from a TLS-pinned GitHub release URL) for resilience to broken pins is
the correct call here.

This policy **differs from `install-pi.sh`**, which still pins
`RMAPI_VERSION` and `RMAPI_SHA256`. Bumping or dropping the Pi pin is a
separate concern with its own validation surface (the Pi is the
production path) and is **not delivered by this spec**. It is recorded
as a known follow-up.
