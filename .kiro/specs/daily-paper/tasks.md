# Implementation Plan

## 1. Foundation: package scaffolding and cross-cutting utilities

- [x] 1.1 Create Python package scaffold with pinned dependencies
  - Add `pyproject.toml` declaring the `renewsable` package with `src/` layout, Python ≥ 3.11, `click ≥ 8.1` and `goosepaper == 0.7.1` as runtime deps, `pytest` as dev dep
  - Register the `renewsable` console-script entrypoint pointing at the CLI group
  - Add `src/renewsable/__init__.py` exposing a `__version__` constant and `src/renewsable/__main__.py` delegating to the CLI
  - Add a minimal `.gitignore` covering venv, pytest cache, build artifacts, logs, and output PDFs
  - Observable: `pip install -e .` succeeds in a fresh venv and `renewsable --help` prints the Click group usage (placeholder OK)
  - _Requirements: 10.1, 10.2_

- [x] 1.2 Define the exception hierarchy
  - Implement `RenewsableError` base with `ConfigError`, `BuildError`, `UploadError`, `PairingError`, `ScheduleError` subclasses
  - Each exception carries `message` and optional `remediation` fields; string representation includes both when present
  - Observable: importing each subclass from `renewsable.errors` succeeds and `str(ConfigError("x", remediation="y"))` contains both "x" and "y"
  - _Requirements: 1.3, 1.4, 4.4, 6.4, 10.5_

- [x] 1.3 Implement path resolution helpers
  - Implement default-location resolvers honouring `$XDG_CONFIG_HOME` and `$XDG_STATE_HOME` with `~/.config` / `~/.local/state` fallbacks
  - Expose helpers for: default config path, default output dir, default log dir, systemd user-unit dir, rmapi config file path
  - Observable: unit calls with `XDG_CONFIG_HOME=/tmp/x` return paths rooted at `/tmp/x`; with the env unset they return paths rooted at `~/.config` / `~/.local/state`
  - _Requirements: 1.1, 5.1, 8.2_

- [x] 1.4 Implement logging setup with file + stderr sinks and redaction filter
  - Configure a `RotatingFileHandler` at the default log path (14 backups, daily rotation) plus a stderr `StreamHandler` that journald captures under user units
  - Install a filter that masks substrings matching 8-character uppercase pairing codes and rmapi token prefixes with `***`
  - Expose `configure_logging(config)` called once from the CLI before any component runs
  - Observable: a log record containing a fake token is written with the token replaced by `***` in both the file and stderr sinks
  - _Requirements: 8.1, 8.2, 8.5_

## 2. Core components

- [x] 2.1 Config loader and validator
  - Define a frozen `Config` dataclass with the fields and defaults specified in design.md (schedule_time, output_dir, remarkable_folder, stories, font_size, log_dir, user_agent, goosepaper_bin, rmapi_bin, feed_fetch_retries, feed_fetch_backoff_s, upload_retries, upload_backoff_s, subprocess_timeout_s)
  - Implement `Config.load(path)` reading JSON and `Config.validate()` raising `ConfigError` whose message names both the config file path and the offending field
  - Validate `schedule_time` against `^\d{2}:\d{2}$` and that it parses via `datetime.time.fromisoformat`; require non-empty `stories`; expand paths to absolute
  - Observable: loading `fixtures/config.valid.json` returns a populated `Config`; loading `fixtures/config.missing_field.json` raises `ConfigError` with the field name
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_
  - _Boundary: Config_
  - _Depends: 1.2, 1.3_

- [ ] 2.2 (P) Scheduler: render and install/uninstall systemd user units
  - Add `templates/renewsable.service.tmpl` and `templates/renewsable.timer.tmpl` in the package, using `string.Template` placeholders for `exe_path`, `home`, `schedule_time`
  - Implement `Scheduler.install()` rendering both units into the systemd user directory, running `systemctl --user daemon-reload`, then `systemctl --user enable --now renewsable.timer`; idempotent on re-run
  - Implement `Scheduler.uninstall()` running `systemctl --user disable --now renewsable.timer`, deleting the unit files, then `daemon-reload`; idempotent when already uninstalled
  - Implement `Scheduler.status()` returning a short string derived from `systemctl --user list-timers`
  - Observable: after `install()` against a temp `XDG_CONFIG_HOME`, both unit files exist with the configured time substituted; after `uninstall()` both files are gone
  - _Requirements: 5.1, 5.2, 5.5, 5.6_
  - _Boundary: Scheduler_
  - _Depends: 1.2, 1.3, 2.1_

- [ ] 2.3 (P) Pairing helper for first-run rmapi setup
  - Implement `Pairing.is_paired()` checking for a non-empty rmapi config file at the resolved path
  - Implement `Pairing.pair(force=False)` that returns early when `is_paired()` unless `force=True`, otherwise spawns `rmapi` with inherited stdin/stdout/stderr so the user enters the one-time code directly
  - After the spawn exits, verify the token file is now present; raise `PairingError` with a remediation hint otherwise
  - Ensure no logger call ever includes the token or the code (rely on the redaction filter from 1.4 as defence in depth)
  - Observable: with a pre-populated rmapi config file, `pair()` returns without spawning; with an empty config path, `pair()` invokes the configured rmapi binary and raises `PairingError` when the fake exits without writing a token
  - _Requirements: 6.2, 6.3, 8.5_
  - _Boundary: Pairing_
  - _Depends: 1.2, 1.3, 2.1_

- [ ] 2.4 (P) Builder: feed pre-fetch, temp config, goosepaper subprocess
  - Implement per-host `robots.txt` check (cached for the run) using `urllib.robotparser`; skip disallowed hosts
  - Implement `_fetch_with_retry(url)` using `config.user_agent`, with bounded retries (`config.feed_fetch_retries`) and exponential backoff starting at `config.feed_fetch_backoff_s`; returns bytes or raises after exhaustion
  - For each `rss` story in the config, pre-fetch the feed to a temp file inside a per-run `TemporaryDirectory`, rewrite the `rss_path` in a copied goosepaper-subset config to the local `file://` URL
  - If a feed exhausts retries or is disallowed, log the source URL and reason and continue; do not fail the build
  - Invoke `goosepaper -c <tmp.json> -o <output_dir>/renewsable-<YYYY-MM-DD>.pdf` with `config.subprocess_timeout_s`; capture stderr to the logger
  - After goosepaper exits, validate the output file exists, is non-empty, and starts with `%PDF-`; raise `BuildError` if goosepaper exited non-zero, if every pre-fetch failed, or if the PDF is missing/invalid
  - Observable: a successful run produces `<output_dir>/renewsable-YYYY-MM-DD.pdf` matching today's local date; a run where every feed fails raises `BuildError` and leaves no partial file
  - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 3.1, 3.3, 3.4, 8.3, 9.1, 9.2, 9.4_
  - _Boundary: Builder_
  - _Depends: 2.1_

- [ ] 2.5 (P) Uploader: rmapi mkdir + put --force with bounded retries
  - Implement `Uploader.upload(pdf, folder=None)` defaulting `folder` to `config.remarkable_folder`
  - Run `rmapi mkdir <folder>`; treat exit-code-non-zero with "already exists" stderr pattern as success; any other failure raises `UploadError`
  - Run `rmapi put --force <pdf> <folder>/` with bounded retries (`config.upload_retries`) and exponential backoff starting at `config.upload_backoff_s`, retrying only on non-token failure classes
  - Classify rmapi stderr into `token`, `network`, `other`; token-class errors raise `UploadError` whose remediation hint is "run `renewsable pair`" and are not retried
  - On final failure, raise `UploadError` naming the folder, the local PDF path, and the redacted captured stderr; leave the local PDF in place
  - Observable: given a fake `rmapi` exit sequence `1,1,0`, the upload succeeds after two retries; given exit `1` with token-pattern stderr, the upload raises `UploadError` on the first attempt
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 6.4, 8.4, 9.3, 9.5_
  - _Boundary: Uploader_
  - _Depends: 2.1_

## 3. Integration: CLI, example config

- [ ] 3.1 Implement the Click CLI group with all subcommands
  - Create a Click group `renewsable` with global `--config PATH` and `--log-level LEVEL` options
  - Implement subcommands: `build`, `upload [PATH]`, `run`, `install-schedule`, `uninstall-schedule`, `pair [--force]`, `test-pipeline`
  - Each command: load config (exit 2 on `ConfigError`), call `configure_logging(config)`, instantiate the relevant component, call the method, translate `RenewsableError` subclasses to exit code 1 with stderr message, exit 0 on success
  - `run` sequences `Builder.build()` then `Uploader.upload()`, short-circuiting with exit 1 on `BuildError` and never calling upload in that case
  - `upload` without a PATH argument uploads today's built PDF; with a PATH argument uploads that file to the configured folder without rebuilding
  - `test-pipeline` calls `run` once with loud logging so the user can verify the full pipeline end-to-end on demand
  - Observable: `renewsable build --help` prints a usage summary; `renewsable run` with a valid config produces a PDF and a rmapi invocation; `renewsable build` with a missing config file exits 2 and names the expected path
  - _Requirements: 1.1, 1.3, 4.1, 4.5, 6.5, 10.1, 10.2, 10.3, 10.4, 10.5_
  - _Depends: 2.1, 2.2, 2.3, 2.4, 2.5_

- [ ] 3.2 Ship an example configuration with telex.hu and the English defaults
  - Add `config/config.example.json` populated with `schedule_time: "05:30"`, `remarkable_folder: "/News"`, `output_dir` absent (use default), and a `stories` array including telex.hu RSS plus BBC World, NYT Homepage, Guardian International, Economist weekly, and Hacker News front page
  - Include inline-commented notes (as README-adjacent documentation, not JSON comments) describing each top-level field and referencing goosepaper's `stories` schema for provider-specific fields
  - Observable: `renewsable build --config config/config.example.json --dry-run` style invocation loads and validates the file without raising `ConfigError`
  - _Requirements: 1.2, 1.6, 2.4, 3.1_

## 4. Pi bootstrap and deployment workflow

- [ ] 4.1 Implement the Pi bootstrap script
  - Add `scripts/install-pi.sh` (bash, `set -euo pipefail`) targeting Raspberry Pi OS Bookworm 64-bit
  - Steps: verify 64-bit arch and fail loudly if armhf; `apt install` the WeasyPrint and font package list (`python3-dev python3-pip python3-cffi python3-brotli libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi-dev shared-mime-info fonts-dejavu fonts-noto-core`); create a venv under the project; install the package in editable mode; download the pinned `rmapi-linux-arm64` release tarball, verify checksum, install `rmapi` into the venv `bin/`
  - Print a clear next-steps block instructing the user to run `renewsable pair`, then `renewsable test-pipeline`, then `renewsable install-schedule`, and finally `sudo loginctl enable-linger $USER`
  - Observable: on a fresh Pi the script exits 0, the venv contains `renewsable` and `rmapi` executables, and `renewsable --help` runs cleanly
  - _Requirements: 6.1, 7.1, 7.2, 7.3_

- [ ] 4.2 Write the README with setup and deployment workflow
  - Document the ordered Pi setup: run `install-pi.sh`, pair, test-pipeline, install-schedule, enable-linger
  - Document the deployment workflow from macOS: push to git, SSH to Pi, `git pull`, `pip install -e .`, `renewsable install-schedule` if the schedule time changed
  - Include a configuration reference table listing every top-level field, its type, and its default
  - Include a troubleshooting section covering token loss, one-time-code re-pairing, and `journalctl --user -u renewsable.service` inspection
  - Note the 64-bit Pi OS prerequisite and the `sudo loginctl enable-linger $USER` manual step
  - Observable: the README contains a complete runnable command sequence for zero-to-scheduled-run, plus references to `config/config.example.json`
  - _Requirements: 5.3, 6.1, 7.1, 7.3, 7.4_

## 5. Automated tests

- [ ] 5.1 (P) Unit tests for Config
  - Load the valid fixture and assert the parsed dataclass fields, applied defaults, and absolute paths
  - Load the missing-field fixture and assert `ConfigError` is raised with a message naming the field
  - Load a fixture with malformed `schedule_time` and assert `ConfigError` whose message names `schedule_time`
  - Observable: `pytest tests/test_config.py` green with at least four assertions across the three scenarios
  - _Requirements: 1.2, 1.3, 1.4, 1.6_
  - _Boundary: Config_

- [ ] 5.2 (P) Unit tests for Scheduler template rendering and install flow
  - Assert `_render_units` produces byte-identical output against a golden string for a given schedule time and exe path
  - Run `install()` against a temp `XDG_CONFIG_HOME` with `systemctl` calls mocked; assert both unit files exist with expected content
  - Run `uninstall()` from the installed state; assert both unit files are removed and `systemctl --user daemon-reload` was called
  - Observable: `pytest tests/test_scheduler.py` green including golden-file comparison
  - _Requirements: 5.1, 5.2, 5.5_
  - _Boundary: Scheduler_

- [ ] 5.3 (P) Integration tests for Builder with a fake goosepaper
  - Add `tests/fixtures/fake_goosepaper.sh` that accepts `-c` and `-o` and emits a stub PDF file starting with `%PDF-` at the output path
  - Configure `goosepaper_bin` to point at the fake; assert the produced PDF lives at `<output_dir>/renewsable-<today>.pdf`
  - Add a test where one fake feed URL (served by an in-test HTTP stub on localhost) returns 503 three times; assert the Builder logs a retry-exhaustion entry, skips the source, and still produces a PDF from remaining sources
  - Add a test where every feed fails; assert `BuildError` is raised and no PDF is left in the output dir
  - Observable: `pytest tests/test_builder.py` green covering success, per-feed retry, and all-fail paths
  - _Requirements: 2.1, 2.3, 2.6, 9.1, 9.2_
  - _Boundary: Builder_

- [ ] 5.4 (P) Integration tests for Uploader with a fake rmapi
  - Add `tests/fixtures/fake_rmapi.sh` that records each argv invocation to a file and returns an exit code from a queue
  - Assert `mkdir /News` is called before `put --force`; assert a successful upload yields no `UploadError`
  - Assert exit sequence `1,1,0` succeeds after two retries
  - Assert a token-pattern stderr on the first attempt raises `UploadError` with `remediation` pointing to `renewsable pair` and no retries occur
  - Observable: `pytest tests/test_uploader.py` green covering happy path, retry, and token-error path
  - _Requirements: 4.2, 4.3, 4.4, 6.4, 9.3_
  - _Boundary: Uploader_

- [ ] 5.5 (P) CLI command tests using Click's CliRunner
  - Assert `renewsable --help` and each subcommand's `--help` exit 0 and print usage
  - Assert `renewsable build --config does-not-exist.json` exits 2 and stderr names the expected path
  - Assert `renewsable run` wires `Builder.build` and `Uploader.upload` in order, short-circuiting when `Builder.build` raises `BuildError` (components patched)
  - Assert `renewsable upload /tmp/x.pdf` calls `Uploader.upload` with the explicit path without invoking Builder
  - Observable: `pytest tests/test_cli.py` green covering help, config error, run sequencing, and explicit upload
  - _Requirements: 1.3, 4.1, 4.5, 10.1, 10.2, 10.3, 10.4, 10.5_
  - _Boundary: CLI_

- [ ] 5.6 (P) Logging redaction test
  - Configure logging to a tempfile; emit a record whose message contains an 8-char uppercase code and a string matching the rmapi token pattern
  - Assert neither the code nor the token appears in the tempfile or captured stderr; `***` appears in place of each
  - Observable: `pytest tests/test_logging.py` green with both secret patterns redacted
  - _Requirements: 8.5_
  - _Boundary: logging_setup_

## 6. End-to-end validation on the Pi

- [ ] 6.1 End-to-end smoke test against the real tooling
  - On the Pi, after running `install-pi.sh` and `renewsable pair`, execute `renewsable test-pipeline --config config/config.example.json`
  - Verify a dated PDF appears in the configured output directory; verify a document with the same name appears in the `/News/` folder on the reMarkable cloud (via `rmapi ls /News` or the tablet itself)
  - Install the schedule with a temporary fire time two minutes in the future; wait for the fire; verify `journalctl --user -u renewsable.service --since "2 minutes ago"` shows the run and the log file records it; restore the real schedule afterwards
  - Observable: a checklist pass recorded in the README troubleshooting/verification section with the command output excerpts
  - _Requirements: 3.2, 5.1, 5.3, 5.4, 6.5, 8.1, 8.2_
  - _Depends: 3.1, 4.1, 4.2_

## Implementation Notes

- **goosepaper dependency**: PyPI `goosepaper==0.7.1` does not exist (only 0.7.0, which has a broken sdist missing `requirements.txt`). Pin via git tag: `goosepaper @ git+https://github.com/j6k4m8/goosepaper@v0.7.1`. Confirmed installable on Python 3.11 macOS arm64 with `rmapy` as a transitive runtime import (goosepaper's `__main__` imports upload.py which imports rmapy). `rmapy` must be installed even though we never use goosepaper's upload path.
- **Rotation primitive**: `TimedRotatingFileHandler(when='midnight', interval=1, backupCount=14)` satisfies "14 days of runs" (Req 8.2) better than size-based `RotatingFileHandler` suggested in task 1.4 text. Deviation documented in `src/renewsable/logging_setup.py` docstring.
- **Filter placement**: Redaction filter must be attached to each handler, not the logger, so filters run after record formatting across thread boundaries.
- **Config.load(path) requires a non-None path**: design diagram annotates `Path | None` but the implementation expects the CLI (task 3.1) to resolve `paths.default_config_path()` before calling `Config.load`. Task 3.1 must do this, e.g. `Config.load(args.config or paths.default_config_path())`.
