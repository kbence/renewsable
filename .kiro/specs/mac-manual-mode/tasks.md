# Implementation Plan

- [ ] 1. Foundation: scheduler test seam
- [x] 1.1 Establish a sys.platform test seam and autouse linux fixture for the scheduler tests
  - Expose the standard library sys module as a module-level alias on the scheduler component so the platform value can be monkeypatched the same way the existing subprocess seam is
  - Add an autouse pytest fixture to the scheduler test module that pins the scheduler-side platform alias to "linux" before every test, so the existing scheduler test suite remains green on the macOS dev box once platform branches land
  - Observable: the existing scheduler test suite passes unchanged on the macOS dev box, and a downstream test can override the autouse fixture inline by setting the alias to "darwin"
  - _Requirements: 2.4_
  - _Boundary: Scheduler module and its test module_

- [ ] 2. Scheduler platform refusal
- [x] 2.1 Implement the macOS refusal helper and wire it into install, uninstall, and status
  - Introduce a helper that raises a ScheduleError when the scheduler-side platform alias reports darwin, with remediation copy that names "renewsable run" and "renewsable test-pipeline" as the manual entrypoints
  - Call the helper as the first executable statement of install, uninstall, and status, before any subprocess invocation, unit-file write, or filesystem read
  - Linux behavior must remain bit-identical to the pre-spec implementation: no new branches in the happy path, no changes to template rendering, the systemctl helper, or the missing-unit detection
  - Observable: on Darwin, all three scheduler methods raise ScheduleError before mutating filesystem state or invoking systemctl; on Linux, install, uninstall, and status produce the same outputs and side-effects as before
  - _Requirements: 2.1, 2.2, 2.3, 2.4_
  - _Depends: 1.1_
  - _Boundary: Scheduler_

- [x] 2.2 Add unit tests covering install, uninstall, and status refusal on Darwin
  - Three new test cases, one each for install, uninstall, and status, that override the autouse fixture inline by setting the scheduler-side platform alias to "darwin", patch a recording subprocess fake, and exercise the corresponding method
  - Each test asserts the method raises ScheduleError, the message text names "renewsable run" and "renewsable test-pipeline", and the recording subprocess fake never recorded a call
  - The status case is included even though status is not currently wired to a CLI command, to lock in the design's defensive symmetry decision and prevent a future regression from silently dropping the refusal call
  - Observable: pytest reports all three new test cases passing alongside the existing scheduler suite
  - _Requirements: 2.1, 2.2, 2.3_
  - _Depends: 2.1_

- [x] 3. (P) macOS bootstrap script
  - Implement an idempotent macOS bootstrap shell script that supports both Apple Silicon and Intel Macs and mirrors the Pi bootstrap shape but: rejects non-Darwin hosts with a platform-naming guidance message and exits non-zero without modifying the working directory; resolves the host architecture from `uname -m` to `arm64` or `x86_64` and refuses any other value on Darwin with an unsupported-architecture message; verifies the operator has Python 3.11 or newer on PATH and exits with a Homebrew-pointing remediation otherwise; creates a project-local virtualenv when absent and reuses it when present; installs renewsable in editable mode with dev extras into that venv; downloads the latest `ddvk/rmapi` macOS asset matching the resolved architecture (`rmapi-macos-arm64.zip` for Apple Silicon, `rmapi-macos-intel.zip` for Intel) from the GitHub `releases/latest/download/<asset>` URL pattern; on download or extraction failure aborts before installing the binary and prints a message naming the source URL or extraction target; extracts the zip via `unzip`; installs the rmapi executable into the venv's bin directory with mode 0755; defensively clears the macOS quarantine xattr on the installed binary so first-run Gatekeeper does not block pairing; smoke-tests both the renewsable entrypoint and the rmapi binary; prints a next-steps banner that explicitly notes no scheduler is installed
  - The script must use only macOS-shipped tools (bash 3.2 compatible, curl, unzip, install, mktemp, xattr, uname, python3) and must not invoke apt, sudo, or brew
  - **No version pin and no SHA-256 pin** for the rmapi download. The script trusts the upstream release; this is a deliberate decision capturing the reviewer's input that pinning has historically failed (the Pi-side `v0.0.32` pin is broken in production today against the live reMarkable cloud) and that for a single-operator project, getting stuck on a known-bad pinned binary is the higher-impact failure mode. The rationale is recorded in `research.md` and `scripts/README.md` (Task 4.2).
  - Re-running the script on a fully bootstrapped host completes successfully without redownloading the rmapi archive or recreating the venv; the quarantine-clear step is re-run defensively in this branch
  - Asset name resolution lives in a single `case "$ARCH" in ... ;; esac` block; this is the only place upstream asset names appear in the script
  - Observable: on a clean Apple Silicon Mac, running the script exits 0 and the venv's renewsable command responds to --help, and the script downloaded `rmapi-macos-arm64.zip`; on a clean Intel Mac the same is true and the script downloaded `rmapi-macos-intel.zip`; on any non-Darwin host the script exits non-zero with a platform-naming message and does not create a venv; re-running on a fully bootstrapped host completes within seconds without network calls
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_
  - _Boundary: scripts/install-mac.sh_

- [ ] 4. Documentation
- [x] 4.1 (P) Update the project README with the macOS install path
  - Add a one-paragraph disambiguator at the top of the setup region that introduces the two install paths (Pi for scheduled production, Mac for manual on-demand) and points the reader to the appropriate section
  - Append a new "Setup on macOS (manual mode)" section after the existing Pi runbook that walks the operator through bootstrap → pair → test-pipeline → run, using the same fenced bash code-block style as the Pi runbook, and references the existing Customise the config block instead of restating it
  - Include an explicit callout in the macOS section that install-schedule and uninstall-schedule exit non-zero on macOS by design, that no launchd or cron integration is provided, and that the operator must invoke renewsable run themselves
  - Leave the existing Pi runbook's step ordering and prose untouched
  - Observable: the README renders with the disambiguator paragraph above the setup region, the existing Pi section unchanged, the new macOS section in the order bootstrap → pair → test-pipeline → run, and the no-scheduling callout visible
  - _Requirements: 4.1, 4.2, 4.3, 4.4_
  - _Boundary: README.md_

- [x] 4.2 (P) Document the macOS rmapi-fetch policy and fix the stale Pi-script comment
  - Append a paragraph to `scripts/README.md` documenting the macOS rmapi-fetch policy: latest release via `https://github.com/ddvk/rmapi/releases/latest/download/<asset>`, asset chosen from `uname -m` (`rmapi-macos-arm64.zip` for Apple Silicon, `rmapi-macos-intel.zip` for Intel), no version or SHA-256 pin, plus the defensive `xattr -d com.apple.quarantine` step
  - Capture the rationale for skipping the pin: the Pi-side `v0.0.32` pin is broken in production today (sync-v3 invalid hash, fixed upstream in `v0.0.33`); for a one-operator project, getting stuck on a known-bad pinned binary has historically been a higher-impact failure mode than upstream tampering
  - State explicitly that this differs from the Pi script's policy (which still pins) and that bumping or dropping the Pi pin is a separate concern not delivered by this spec
  - Replace the stale "ships only linux binaries" claim in `scripts/install-pi.sh`'s host-check comment with an accurate one-line summary noting upstream ships Linux, macOS, and Windows binaries today
  - Add a one-line maintainer comment near the rmapi version block of `scripts/install-pi.sh` pointing readers at `scripts/install-mac.sh` for the macOS counterpart, with a note that the two scripts intentionally use different fetch policies (Mac: latest; Pi: pinned) — no behavior change to the Pi script
  - Observable: a maintainer reading either bootstrap script sees the cross-reference and the corrected platform-coverage comment; `scripts/README.md` documents the macOS fetch policy, the rationale, and how it differs from the Pi pinning policy
  - _Requirements: 1.2, 1.3_
  - _Boundary: scripts/README.md and scripts/install-pi.sh_

- [ ] 5. Validation
- [ ] 5.1 End-to-end verification on macOS hardware
  - Run the macOS bootstrap on a clean Apple Silicon Mac and confirm it succeeds; immediately re-run it and confirm idempotency (no redownload, no venv recreation, exit 0); confirm the script resolved `arm64` from `uname -m` and downloaded `rmapi-macos-arm64.zip`
  - If an Intel Mac is available (opportunistic), run the same walk on it and confirm the script resolved `x86_64` and downloaded `rmapi-macos-intel.zip`. Treat Intel coverage as best-effort given the project's primary operator targets Apple Silicon
  - Run renewsable pair, complete the reMarkable cloud pairing flow with a real one-time code, and confirm a non-empty token file is written at the user's rmapi configuration path
  - Run renewsable test-pipeline against the example config and confirm a dated EPUB appears both in the configured output directory and in the configured reMarkable folder
  - Run renewsable install-schedule and renewsable uninstall-schedule on the Mac; confirm both exit non-zero with a message that names the manual entrypoints, and that no LaunchAgents plist, no launchctl invocation, and no other host-scheduler state is touched
  - Observable: all checks succeed on Apple Silicon real hardware; any Apple Silicon failure is a hard stop before merge; Intel-Mac coverage is recorded if attempted but not blocking
  - _Requirements: 1.1, 1.2, 1.5, 2.1, 2.2, 3.1, 3.2, 3.3_

- [ ] 5.2 Pi non-regression verification
  - On a Raspberry Pi running Pi OS Bookworm 64-bit (or the existing production Pi), check out this branch and run pip install -e . inside the existing venv
  - Run renewsable install-schedule and confirm the systemd user timer is installed and fires at the configured schedule_time; subsequently run renewsable uninstall-schedule and confirm the timer is removed
  - Run renewsable run and confirm a dated EPUB is built locally and uploaded to the configured reMarkable folder
  - Run the full pytest suite on the Pi (or in a Linux container that matches the Pi runtime) and confirm zero failures
  - Observable: the Pi deployment behaves identically to before this spec, the daily timer continues to work end-to-end, and pytest reports zero failures
  - _Requirements: 5.1, 5.2, 5.3, 5.4_

- [x] 6. Make `paths.rmapi_config_path()` platform-aware (scope expansion forced by Task 5.1 verification)
  - Surfaced live during Task 5.1 on a clean macOS host: `renewsable pair` failed with `no token at ~/.config/rmapi/rmapi.conf` even though rmapi v0.0.33 had successfully paired. Root cause: rmapi (Go) writes its config via `os.UserConfigDir()`, which is `~/Library/Application Support/rmapi/rmapi.conf` on macOS, not the XDG path `paths.rmapi_config_path()` was hardcoded to.
  - Add a module-level `sys` alias on `paths.py` (same test seam pattern as `scheduler.sys`); make `rmapi_config_path()` honour `$RMAPI_CONFIG` first, then branch on `sys.platform == "darwin"` to return the Library/Application Support path, falling back to the existing XDG behavior on Linux/other.
  - Tests: `TestRmapiConfigPathDarwin` (two cases — macOS uses Library/Application Support; macOS ignores `XDG_CONFIG_HOME`); `TestRmapiConfigPathOverride` (three cases — `$RMAPI_CONFIG` overrides on Linux, on Darwin, empty value falls back). Pin `paths.sys.platform = "linux"` via an autouse fixture so existing XDG-branch tests remain deterministic on the macOS dev box. Update `tests/test_pairing.py::xdg_tmp` to pin platform and clear `$RMAPI_CONFIG` so existing pairing tests stay green.
  - Observable: on the macOS dev box, `renewsable --config config/config.example.json pair` exits 0 with `pairing complete` against the existing `~/Library/Application Support/rmapi/rmapi.conf`; full suite reports 255 passed (250 + 5 new path tests).
  - _Requirements: 3.1_
  - _Boundary: src/renewsable/paths.py, tests/test_paths.py, tests/test_pairing.py_
