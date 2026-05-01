# Gap Analysis — mac-manual-mode

## 1. Current State Investigation

### Code surface relevant to this spec

| Asset | File | Role | Mac-readiness |
|---|---|---|---|
| Pi bootstrap | `scripts/install-pi.sh` (207 lines) | Linux/aarch64 host check, apt deps, venv, pinned linux-arm64 `rmapi` download with SHA-256 pin, smoke test | Needs a parallel, not a port |
| Pi bootstrap docs | `scripts/README.md` | Explains the install-pi.sh contract and how to bump the rmapi pin | Sibling needed for Mac |
| Scheduler | `src/renewsable/scheduler.py` | Renders `renewsable.service` / `renewsable.timer` from templates, calls `systemctl --user` via a module-level `subprocess` alias | Needs a platform refusal seam |
| CLI | `src/renewsable/cli.py` | `install-schedule` / `uninstall-schedule` simply construct `Scheduler(config, exe_path).install()` and translate `RenewsableError` to exit 1 | Refusal can live in CLI **or** Scheduler |
| Pairing | `src/renewsable/pairing.py` | Spawns `rmapi`, inherits stdio, checks `~/.config/rmapi/rmapi.conf` for non-empty token. No platform branches anywhere. | Already cross-platform |
| Builder / Uploader / Articles / EPUB / HTTP / Config / Logging / Paths | `src/renewsable/*.py` | Pure Python, XDG-aware paths via `paths.py`, no subprocess outside `Pairing` and `Uploader` | Already cross-platform |
| Tests | `tests/test_*.py` | Subprocess is mocked everywhere (`test_scheduler.py:15` explicitly notes "systemctl does not exist on the macOS dev box") | Existing suite already runs on macOS — no platform skips needed |

### Key conventions to preserve

- **Subprocess seams**: every external-process boundary (`scheduler.subprocess`, `pairing.subprocess`, `uploader.subprocess`) is exposed as a module-level alias so tests can `monkeypatch.setattr` once. Any new platform branch must keep this discipline so a Mac CI run can mock `sys.platform` cleanly.
- **XDG paths**: `paths.py` already works on macOS — `~/.config/...` resolves the same way regardless of OS. `systemd_user_unit_dir()` returns a path on macOS too, but writing to it would be meaningless; the platform refusal must short-circuit before that path is written.
- **Idempotent bootstrap**: `install-pi.sh` re-runs cleanly (skip apt if installed, skip venv if present, skip rmapi if binary already exists). The Mac script must follow the same idempotency contract.
- **Pinned rmapi via SHA-256**: `install-pi.sh` embeds `RMAPI_VERSION="v0.0.32"` and `RMAPI_SHA256=...` and aborts on mismatch. The Mac script must mirror this pattern.

## 2. Requirements → Asset Map (with gaps)

| Requirement | Existing assets | Gap | Tag |
|---|---|---|---|
| 1.1 Create venv + editable install on Darwin/arm64 | `install-pi.sh:111-128` (venv + editable install block) | New script reusing the same 17-line pattern; apt block omitted | **Missing** (new script) |
| 1.2 Download pinned darwin-arm64 `rmapi` with SHA-256 | `install-pi.sh:130-162` (download + verify + extract + install) | macOS asset is `rmapi-macos-arm64.zip` — **zip, not tar.gz**, **macos, not darwin** in the filename. Needs `unzip` (built-in on macOS) instead of `tar -xzf`. Also a fresh SHA-256 pin. | **Missing** + **Constraint** (asset format) |
| 1.3 SHA-256 mismatch aborts | `install-pi.sh:148` (sha256sum check) | macOS uses `shasum -a 256` (BSD coreutils) — `sha256sum` exists only with GNU coreutils via Homebrew. Must use `shasum -a 256 -c` for portability. | **Constraint** (BSD vs GNU coreutils) |
| 1.4 Reject non-Darwin/arm64 hosts | `install-pi.sh:55-69` (Linux + aarch64 gate, with explicit Darwin rejection) | Symmetric: detect Darwin + arm64; reject Linux, Darwin/x86_64, etc. | **Missing** |
| 1.5 Idempotent re-run | `install-pi.sh:111-114, 133-134` (existence checks) | Same pattern reused | **Missing** (mechanical) |
| 1.6 No `apt-get` / `sudo` | n/a | Negative requirement — scrub script of any inherited apt logic | **Missing** (mechanical) |
| 2.1 / 2.2 `install-schedule` / `uninstall-schedule` exit non-zero on macOS with guidance | `cli.py:298-333`, `scheduler.py:88-147` | New platform refusal. **Two viable insertion points** (see Option A vs B below). The error message must list `renewsable run` and `renewsable test-pipeline` as the manual entrypoints. | **Missing** (new behavior) |
| 2.3 No `systemctl`/`launchctl`/`crontab` invoked on macOS | `scheduler.py` calls `systemctl` unconditionally today | If refusal lives in `Scheduler` it must short-circuit **before** `_run_systemctl`; if it lives in CLI it short-circuits before `Scheduler(...).install()` is even constructed. | **Missing** |
| 2.4 Linux behavior unchanged | Existing `Scheduler` + `cli` paths | Add platform branch without altering the Linux happy path. Existing `tests/test_scheduler.py` and `tests/test_cli.py` must keep passing untouched. | **Constraint** (no regression) |
| 3.1 / 3.2 / 3.3 Pipeline parity | `Pairing`, `Builder`, `Uploader`, `cli.run`, `cli.test_pipeline` | None — these are already platform-agnostic. Verification is "the existing CLI commands still work after `install-mac.sh`". | **None** (verification only) |
| 4.1–4.4 README macOS section parallel to Pi | `README.md` | Add a sibling section. Two layout options (see Option C vs D below). | **Missing** (docs) |
| 5.1–5.4 Pi path remains operational | `install-pi.sh`, `Scheduler`, `cli` | Negative requirement — verified by leaving Linux paths untouched and re-running the existing test suite. | **Constraint** |

### Research-needed items

- **R1: rmapi pin choice** — `v0.0.32` keeps parity with the Pi pin and uses the same upstream cut for both platforms. `v0.0.33` is the current latest. **Recommendation:** pin `v0.0.32` so a Pi/Mac mixed deployment shares the same rmapi behavior; bump in lockstep when Pi bumps. Mechanical lookup of the darwin-arm64 SHA-256 happens at script-write time (one `shasum -a 256 rmapi-macos-arm64.zip`).
- **R2: Python version floor** — `pyproject.toml` requires `>=3.11`. macOS Sequoia (14.x / 15.x) system Python is older. The Mac script must verify `python3 --version` ≥ 3.11 and fail clearly with a Homebrew install hint. The Pi script doesn't have this check because Bookworm ships 3.11; Mac inherits the constraint and needs a guard.
- **R3: SHA verification tool** — macOS ships `shasum` (Perl) by default; `sha256sum` is a GNU-coreutils name not present without `brew install coreutils`. The Mac script must use `shasum -a 256 -c` (or `shasum -a 256` + manual compare). The Pi script's `sha256sum --check --status` does not port directly.
- **R4: Archive format** — macOS asset is `.zip`, extracted with `/usr/bin/unzip` (system-shipped). The script must `unzip -q "$TMP/$RMAPI_ZIP" -d "$TMP"`, not `tar -xzf`.

## 3. Implementation Approach Options

### Platform refusal seam — Option A: refusal in CLI

Insert `if sys.platform == "darwin": raise ScheduleError(...)` inside `cli.install_schedule` and `cli.uninstall_schedule`, *before* calling `Scheduler(config, exe_path).install()`.

**Trade-offs**
- ✅ CLI is the user-facing boundary; "this command is unavailable on this OS" is naturally a CLI concern.
- ✅ `Scheduler` stays Linux-only and tests stay focused on its single responsibility.
- ✅ Smallest blast radius: one branch in two CLI callbacks.
- ❌ The CLI now has a platform check, which is a new pattern in this codebase (no other command branches on `sys.platform`).
- ❌ If a third command ever needed platform gating, you'd start to need a helper.

### Platform refusal seam — Option B: refusal in Scheduler

Add a guard at the top of `Scheduler.install()` and `Scheduler.uninstall()`: `if sys.platform == "darwin": raise ScheduleError(...)`.

**Trade-offs**
- ✅ Scheduler is already the "I own systemctl" component; "I don't own anything on macOS" is a natural extension of that responsibility.
- ✅ All existing tests (`tests/test_scheduler.py`) still pass — they already monkeypatch `subprocess.run`. New macOS-specific tests live next to existing scheduler tests.
- ✅ CLI stays trivial.
- ❌ `Scheduler` constructor + attribute access on macOS still happens (cheap, but conceptually slightly wasteful).
- ❌ The error's `remediation=` field is the right shape for a `ScheduleError`, but the wording is more about "use a different command" than "fix the schedule" — a slight semantic stretch.

**Recommendation:** Option B. The scheduler already owns the contract "given a host, install/uninstall a timer"; "given a host that has no timer system we support, refuse" is the same contract. The existing test seam absorbs the new behavior with one new test file or two new test cases.

### Bash structure — Option C: standalone `install-mac.sh`

Independent file that copies the venv + editable-install + smoke-test sections from `install-pi.sh`, swaps the apt block for a Python-version check, and substitutes the macOS rmapi download/extract block.

**Trade-offs**
- ✅ ~100–150 lines of duplication, but the duplicated code is straightforward and platform-specific anyway (host check, package install, archive format, hash tool all differ).
- ✅ Each script reads end-to-end without indirection; bumps to the Pi rmapi pin don't require touching Mac logic and vice versa.
- ✅ Matches the existing project style (single-file install scripts, no shared bash library).
- ❌ Bumping rmapi versions touches two files (mitigation: a comment in each script pointing at the other).

### Bash structure — Option D: extract `install-common.sh` sourced by both

Hoist the venv + editable-install + smoke-test logic into `scripts/install-common.sh`, leave the platform-specific host check and rmapi download in per-platform files.

**Trade-offs**
- ✅ Slightly less duplication for the venv + smoke-test block (~30 lines).
- ❌ Sourced bash libraries are a new pattern in this codebase. Existing scripts/README.md documents a single-file contract.
- ❌ The venv block is already small; extracting it has near-zero leverage.
- ❌ Adds a third file to maintain.

**Recommendation:** Option C (standalone `install-mac.sh`). Duplication is genuinely cheap here; the platform-specific portions dominate, and abstracting the small shared bits hurts readability more than it helps.

### README layout — Option E: append a sibling "macOS (manual mode)" section

Keep the existing "Setup on the Pi (one-time)" section first, add a parallel "Setup on macOS (manual mode)" section after it, with a brief note in the introduction that two install paths exist.

**Trade-offs**
- ✅ Zero churn to the Pi runbook (Req 4.4 explicit constraint).
- ✅ Mac users land on the README and immediately see the platform branch.
- ✅ Section ordering communicates that Pi is the supported production path.

### README layout — Option F: top-level "Choose your install path" branch

Add a "Choose your install path" intro before either runbook, branching to Pi (production, scheduled) vs Mac (manual, on-demand).

**Trade-offs**
- ✅ Symmetric presentation.
- ❌ Restructures the existing README — more user-observable churn for the Pi reader (some friction with Req 4.4's "preserve existing structure").

**Recommendation:** Option E. Minimal churn to the Pi section, clear sibling for Mac.

## 4. Effort & Risk

- **Effort: S (1–3 days).**
  - `install-mac.sh`: ~120–150 lines, mostly modeled on `install-pi.sh`. ~half day.
  - Scheduler refusal + tests: 1 method + 2–3 unit tests. ~2–3 hours.
  - README "macOS (manual mode)" section + intro hint + `scripts/README.md` parallel: ~half day.
  - Manual end-to-end verification on a real Mac (pair, test-pipeline, run): ~1 hour. The corresponding `PI_VERIFICATION.md` analogue is out of scope unless we want one.
- **Risk: Low.**
  - All platform-specific points are well-known (BSD vs GNU coreutils, zip vs tar, macos asset name). No unfamiliar tech.
  - No architectural shift; existing seams (`Scheduler`, `Pairing`, `Uploader`, `paths`) absorb the change without refactoring.
  - The only data lookup needed at script-write time is the SHA-256 of `rmapi-macos-arm64.zip` for the chosen pin — mechanical.

## 5. Recommendations for Design Phase

**Preferred approach**

1. **Refusal in `Scheduler`** (Option B): add `_assert_supported_platform()` called at the top of `install()` and `uninstall()`. Raise `ScheduleError` with `remediation=` pointing at `renewsable run` / `renewsable test-pipeline`. Module-level `sys.platform` import (or a thin `_current_platform()` indirection so tests can monkeypatch it).
2. **Standalone `scripts/install-mac.sh`** (Option C): mirrors `install-pi.sh`'s shape; replaces apt block with a Python-version check; uses `shasum -a 256` and `unzip`; pins `v0.0.32` of `rmapi-macos-arm64.zip` with its SHA-256. Add a one-line cross-reference comment in both scripts noting the rmapi pin should be bumped in lockstep.
3. **README sibling section** (Option E): append "Setup on macOS (manual mode)" after the Pi runbook; add a one-paragraph intro disambiguator at the top of the existing setup section. Walk: bootstrap → pair → test-pipeline → run, with an explicit "no scheduler on macOS — invoke `renewsable run` yourself" callout.
4. **Tests**:
   - `tests/test_scheduler.py`: add `test_install_refuses_on_darwin` and `test_uninstall_refuses_on_darwin` that monkeypatch `scheduler.sys.platform = "darwin"` and assert (a) `ScheduleError` raised, (b) message names `renewsable run` and `renewsable test-pipeline`, (c) **no** `subprocess.run` call observed.
   - No new platform skips needed elsewhere — the suite already runs on macOS today.
5. **Verification doc** (optional, defer): a `MAC_VERIFICATION.md` analogue to `PI_VERIFICATION.md` could be added later. Not required by the requirements; flag as a follow-up.

**Research items to carry into design**

- **R1**: Confirm `v0.0.32` rmapi pin and compute the SHA-256 of `rmapi-macos-arm64.zip` (one shell command at design/implementation time; no actual research).
- **R2**: Decide the Python-version check copy in `install-mac.sh` (point users at `brew install python@3.11` or just `python@3.12`?). Low-stakes wording call.
- **R3**: Decide whether to add `MAC_VERIFICATION.md` now or defer (default: defer; the README walkthrough is sufficient for the spec).
