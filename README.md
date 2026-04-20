# renewsable

A daily news digest for the reMarkable 2, delivered as a PDF every morning by a Raspberry Pi.

Pipeline: configurable RSS feeds → `goosepaper` (HTML + WeasyPrint PDF) → `rmapi` upload to your reMarkable cloud → `systemd` user timer schedules the daily run.

## Status

- Core pipeline (build + upload + scheduled run + pairing) is implemented and unit-tested.
- Bootstrap script (`scripts/install-pi.sh`) provisions apt prerequisites, a project-local venv, and a pinned `rmapi` binary on Raspberry Pi OS Bookworm 64-bit.
- Config loader, logging with credential redaction, per-feed retry/backoff, `systemd --user` timer install/uninstall, and end-to-end `test-pipeline` are all wired.
- Rendering fidelity is whatever `goosepaper`'s default broadsheet template produces; custom CSS is out of scope.
- EPUB output, cross-day deduplication, and any monitoring dashboard are explicit non-goals.

## Prerequisites

- A reMarkable 2 (or Paper Pro) with a logged-in reMarkable cloud account on `my.remarkable.com`.
- A Raspberry Pi running **Raspberry Pi OS Bookworm 64-bit** (`aarch64` / `arm64`). 32-bit Pi OS (`armv7l` / `armhf`) is **not supported** — there is no prebuilt `rmapi` binary for 32-bit ARM and `install-pi.sh` refuses to run.
- The Pi has outbound internet, an accurate clock (default `systemd-timesyncd` is fine), and is kept powered on.
- A development machine (macOS is what this project is built against) with `git` and SSH access to the Pi.

## Setup on the Pi (one-time)

Run these steps in order on the Pi, from a fresh checkout of this repo.

### 1. Get the code onto the Pi

Either clone directly on the Pi, or clone on macOS and `scp -r` it over. A conventional location is `~/renewsable`:

```bash
cd ~
git clone <your-renewsable-remote> renewsable
cd renewsable
```

### 2. Run the bootstrap script

```bash
./scripts/install-pi.sh
```

This installs apt prerequisites for WeasyPrint (Pango, HarfBuzz, FFI, DejaVu + Noto Core fonts), creates a project-local venv at `.venv/`, installs `renewsable` in editable mode with dev extras (`pip install -e ".[dev]"`), and downloads the pinned `ddvk/rmapi` Linux arm64 release tarball with SHA-256 verification into `.venv/bin/rmapi`. It is idempotent: re-running it is safe.

### 3. Pair with your reMarkable cloud account

```bash
source .venv/bin/activate
renewsable pair
```

Open <https://my.remarkable.com/device/desktop/connect> in a browser that is logged into the same reMarkable account, copy the 8-character one-time code, and paste it into the `rmapi` prompt that `renewsable pair` spawns. The device token persists at `~/.config/rmapi/rmapi.conf`; all later runs are headless.

### 4. Smoke-test the full pipeline

```bash
renewsable --config config/config.example.json test-pipeline
```

This runs the full build + upload end-to-end against the shipped example config (six international feeds, `/News` folder on the tablet, 05:30 fire time). On success, a PDF appears at `~/.local/state/renewsable/out/renewsable-YYYY-MM-DD.pdf` and in the `/News/` folder on your reMarkable.

### 5. Customise the config (optional but recommended)

```bash
mkdir -p ~/.config/renewsable
cp config/config.example.json ~/.config/renewsable/config.json
$EDITOR ~/.config/renewsable/config.json
```

Edit the `stories` list and `schedule_time` to taste. See the [Configuration reference](#configuration-reference) below and `config/README.md` for the authoritative field docs. If you skip this step, keep passing `--config config/config.example.json` on every invocation.

### 6. Install the daily schedule

```bash
renewsable install-schedule
```

Add `--config <path>` if you are using a non-default config location. This writes `renewsable.service` and `renewsable.timer` into `~/.config/systemd/user/`, reloads `systemctl --user`, and enables the timer.

### 7. Enable lingering so the timer fires when you are not logged in

```bash
sudo loginctl enable-linger $USER
```

Without this, the user-level `systemd` instance stops when your SSH session ends and the timer never fires. This step is manual because it requires `sudo` and is a one-time system-policy change — the bootstrap script refuses to touch it on your behalf.

You are done. The Pi will build and upload a dated PDF every day at the configured time.

## Deployment workflow (macOS → Pi)

Day-to-day, changes flow via `git`. From the macOS dev box:

```bash
# On macOS
git add -A
git commit -m "..."
git push
```

Then on the Pi:

```bash
# On the Pi
cd ~/renewsable
git pull
.venv/bin/pip install -e .
# Only if schedule_time changed in the config:
.venv/bin/renewsable install-schedule
```

Notes:

- `pip install -e .` is cheap when nothing changed; run it unconditionally after every pull to keep the editable install's metadata in sync (e.g. a new dependency added to `pyproject.toml`).
- `renewsable install-schedule` is idempotent: re-running it overwrites the unit files and reloads `systemctl --user`. Re-running when nothing changed is a no-op you can treat as safe.
- If you only edited feeds in your config, nothing further is needed — the next scheduled fire picks up the change (every `Config.load` is a fresh file read, no caching).
- To roll back, `git checkout <ref>` on the Pi and re-run `pip install -e .`. The config, logs, and output PDFs live outside the repo under `~/.config/renewsable/` and `~/.local/state/renewsable/`.

## Configuration reference

The config is a single JSON object. Unknown top-level keys are rejected to catch typos. Required fields have no default; the loader raises `ConfigError` naming the field and file path if they are missing or malformed. See `src/renewsable/config.py` for the source of truth and `config/README.md` for extended per-field prose.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `schedule_time` | string `"HH:MM"` (24h) | yes | `"05:30"` (dataclass default; example config sets it explicitly) | Local wall-clock time when the daily build fires. |
| `remarkable_folder` | string starting with `/` | yes | `"/News"` | Destination folder on the reMarkable cloud. |
| `stories` | list of `{provider, config}` objects | yes | — (must be non-empty) | `goosepaper` story providers. `renewsable` validates only that it is a non-empty list of objects; per-provider shape is owned by `goosepaper`. |
| `output_dir` | string path (supports `~`) | no | `$XDG_STATE_HOME/renewsable/out` (else `~/.local/state/renewsable/out`) | Where built PDFs are written locally. |
| `font_size` | positive integer | no | `goosepaper` default | Forwarded to `goosepaper` when set. |
| `log_dir` | string path (supports `~`) | no | `$XDG_STATE_HOME/renewsable/logs` (else `~/.local/state/renewsable/logs`) | Where the rotating plain-text log file lands. |
| `user_agent` | string | no | `"renewsable/0.1 (+https://github.com/bnc/renewsable)"` | User-Agent sent on feed fetches. |
| `goosepaper_bin` | string | no | `"goosepaper"` | Command name or absolute path to the `goosepaper` executable. |
| `rmapi_bin` | string | no | `"rmapi"` | Command name or absolute path to the `rmapi` executable. Pi installs drop it at `.venv/bin/rmapi`. |
| `feed_fetch_retries` | integer > 0 | no | `3` | Attempts per feed before giving up (the feed is then skipped). |
| `feed_fetch_backoff_s` | number > 0 | no | `1.0` | Base seconds between feed-fetch retries. |
| `upload_retries` | integer > 0 | no | `3` | Attempts per `rmapi` upload before raising `UploadError`. |
| `upload_backoff_s` | number > 0 | no | `2.0` | Base seconds between upload retries. |
| `subprocess_timeout_s` | integer > 0 | no | `180` | Hard timeout (seconds) for `goosepaper` and `rmapi` subprocess invocations. |

The example at `config/config.example.json` is deliberately minimal: it sets only the required fields plus six pre-configured RSS feeds, and relies on defaults for everything else.

## Daily operation

Nothing. The `systemd --user` timer fires at `schedule_time`, `Builder` fetches feeds and invokes `goosepaper`, `Uploader` invokes `rmapi put --force`, and the paper shows up on the tablet.

To look at what happened:

```bash
# Last run's journald output (service logs)
journalctl --user -u renewsable.service --since today

# Rotating plain-text log file
less ~/.local/state/renewsable/logs/renewsable.log

# Timer state and next fire time
systemctl --user status renewsable.timer
systemctl --user list-timers | grep renewsable
```

To trigger a one-off run on demand:

```bash
renewsable run                 # quiet, same as the scheduled invocation
renewsable test-pipeline       # verbose (at least INFO), prints progress
```

## Troubleshooting

- **Token loss / "paired device was removed from my reMarkable cloud"** — the `rmapi.conf` at `~/.config/rmapi/rmapi.conf` is stale or missing. Re-pair:

  ```bash
  renewsable pair --force
  ```

  Open <https://my.remarkable.com/device/desktop/connect>, type the fresh 8-character one-time code into the `rmapi` prompt. Subsequent runs go back to headless.

- **Timer did not fire / `list-timers` shows `n/a` for `renewsable.timer`** — either lingering is off, or the timer is not enabled.

  ```bash
  systemctl --user status renewsable.timer
  systemctl --user list-timers
  journalctl --user -u renewsable.service --since today
  loginctl show-user $USER | grep Linger    # Linger=yes required
  sudo loginctl enable-linger $USER         # fix if Linger=no
  ```

- **One feed broke the whole build (shouldn't happen)** — by design, per-feed failures are caught and logged; the paper is still built from the surviving feeds. Find the offending feed:

  ```bash
  journalctl --user -u renewsable.service --since today | grep -i 'skip\|fail'
  ```

  Remove the dead feed from your config and commit. If *every* feed failed, `Builder` raises `BuildError` and `renewsable run` exits non-zero before calling `rmapi`.

- **`renewsable pair` can't find `rmapi`** — the shipped install puts it at `.venv/bin/rmapi`. Make sure you activated the venv (`source .venv/bin/activate`) or set `rmapi_bin` in your config to the absolute path.

- **`journalctl --user -u renewsable.service`** is the primary inspection command; pair it with `--since today` or `-f` for a live tail. The plain-text log at `~/.local/state/renewsable/logs/renewsable.log` carries the same records (with credential redaction already applied).

- **apt prerequisites are missing after an OS upgrade** — re-run `./scripts/install-pi.sh`; it is idempotent and will top up any missing packages.

- **Remove the schedule entirely**:

  ```bash
  renewsable uninstall-schedule
  ```

  This disables and stops the timer, deletes the unit files, and reloads `systemctl --user`. Safe to run even when no timer is installed.

- **Wrong config path / `ConfigError: config file not found`** — `renewsable` reads `$XDG_CONFIG_HOME/renewsable/config.json` (falling back to `~/.config/renewsable/config.json`) by default. Override with `--config <path>`. Errors always name the exact file path and the offending field.

## Architecture (one-liner)

Single-process Python orchestrator: `Config → Builder (goosepaper subprocess) → Uploader (rmapi subprocess)`, scheduled by a `systemd --user` timer whose unit files are rendered and installed by the `Scheduler` component. Full design in `.kiro/specs/daily-paper/design.md`; config schema authority in `src/renewsable/config.py` and `config/README.md`.

## License

MIT. This is a personal project; pick a different license before publishing if you feel strongly about it.
