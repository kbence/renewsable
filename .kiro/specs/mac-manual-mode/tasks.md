# Implementation Plan

- [ ] 1. Foundation: scheduler test seam
- [ ] 1.1 Establish a sys.platform test seam and autouse linux fixture for the scheduler tests
  - Expose the standard library sys module as a module-level alias on the scheduler component so the platform value can be monkeypatched the same way the existing subprocess seam is
  - Add an autouse pytest fixture to the scheduler test module that pins the scheduler-side platform alias to "linux" before every test, so the existing scheduler test suite remains green on the macOS dev box once platform branches land
  - Observable: the existing scheduler test suite passes unchanged on the macOS dev box, and a downstream test can override the autouse fixture inline by setting the alias to "darwin"
  - _Requirements: 2.4_
  - _Boundary: Scheduler module and its test module_

- [ ] 2. Scheduler platform refusal
- [ ] 2.1 Implement the macOS refusal helper and wire it into install, uninstall, and status
  - Introduce a helper that raises a ScheduleError when the scheduler-side platform alias reports darwin, with remediation copy that names "renewsable run" and "renewsable test-pipeline" as the manual entrypoints
  - Call the helper as the first executable statement of install, uninstall, and status, before any subprocess invocation, unit-file write, or filesystem read
  - Linux behavior must remain bit-identical to the pre-spec implementation: no new branches in the happy path, no changes to template rendering, the systemctl helper, or the missing-unit detection
  - Observable: on Darwin, all three scheduler methods raise ScheduleError before mutating filesystem state or invoking systemctl; on Linux, install, uninstall, and status produce the same outputs and side-effects as before
  - _Requirements: 2.1, 2.2, 2.3, 2.4_
  - _Depends: 1.1_
  - _Boundary: Scheduler_

- [ ] 2.2 Add unit tests covering install, uninstall, and status refusal on Darwin
  - Three new test cases, one each for install, uninstall, and status, that override the autouse fixture inline by setting the scheduler-side platform alias to "darwin", patch a recording subprocess fake, and exercise the corresponding method
  - Each test asserts the method raises ScheduleError, the message text names "renewsable run" and "renewsable test-pipeline", and the recording subprocess fake never recorded a call
  - The status case is included even though status is not currently wired to a CLI command, to lock in the design's defensive symmetry decision and prevent a future regression from silently dropping the refusal call
  - Observable: pytest reports all three new test cases passing alongside the existing scheduler suite
  - _Requirements: 2.1, 2.2, 2.3_
  - _Depends: 2.1_

- [ ] 3. (P) macOS bootstrap script
  - Implement an idempotent macOS Apple Silicon bootstrap shell script that mirrors the Pi bootstrap shape but: rejects non-Darwin or non-arm64 hosts with a platform-naming guidance message and exits non-zero without modifying the working directory; verifies the operator has Python 3.11 or newer on PATH and exits with a Homebrew-pointing remediation otherwise; creates a project-local virtualenv when absent and reuses it when present; installs renewsable in editable mode with dev extras into that venv; downloads the pinned ddvk/rmapi macos-arm64 zip via curl; verifies the download against an embedded SHA-256 constant via shasum -a 256 -c; on mismatch aborts before installing the binary and prints both the expected checksum and the offending archive name; extracts the verified zip via unzip; installs the rmapi executable into the venv's bin directory with mode 0755; defensively clears the macOS quarantine xattr on the installed binary so first-run Gatekeeper does not block pairing; smoke-tests both the renewsable entrypoint and the rmapi binary; prints a next-steps banner that explicitly notes no scheduler is installed
  - The script must use only macOS-shipped tools (bash 3.2 compatible, curl, unzip, shasum, install, mktemp, xattr, python3) and must not invoke apt, sudo, or brew
  - Re-running the script on a fully bootstrapped host completes successfully without redownloading the rmapi archive or recreating the venv; the quarantine-clear step is re-run defensively in this branch
  - Compute the embedded SHA-256 constant by running shasum -a 256 against the v0.0.32 macos-arm64 release asset at script-write time, matching the Pi script's rmapi version pin for cross-platform parity
  - Observable: on a clean Apple Silicon Mac, running the script exits 0 and the venv's renewsable command responds to --help; on any non-Apple-Silicon host the script exits non-zero with a platform-naming message and does not create a venv; re-running on a fully bootstrapped host completes within seconds without network calls
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_
  - _Boundary: scripts/install-mac.sh_

- [ ] 4. Documentation
- [ ] 4.1 (P) Update the project README with the macOS install path
  - Add a one-paragraph disambiguator at the top of the setup region that introduces the two install paths (Pi for scheduled production, Mac for manual on-demand) and points the reader to the appropriate section
  - Append a new "Setup on macOS (manual mode)" section after the existing Pi runbook that walks the operator through bootstrap → pair → test-pipeline → run, using the same fenced bash code-block style as the Pi runbook, and references the existing Customise the config block instead of restating it
  - Include an explicit callout in the macOS section that install-schedule and uninstall-schedule exit non-zero on macOS by design, that no launchd or cron integration is provided, and that the operator must invoke renewsable run themselves
  - Leave the existing Pi runbook's step ordering and prose untouched
  - Observable: the README renders with the disambiguator paragraph above the setup region, the existing Pi section unchanged, the new macOS section in the order bootstrap → pair → test-pipeline → run, and the no-scheduling callout visible
  - _Requirements: 4.1, 4.2, 4.3, 4.4_
  - _Boundary: README.md_

- [ ] 4.2 (P) Document the macOS rmapi pin and add a cross-script bump reminder
  - Append a paragraph to the scripts README documenting the macOS rmapi pin: version, archive filename, embedded SHA-256, the bump procedure via shasum -a 256, and the defensive quarantine-clear note
  - State explicitly in that paragraph that the Pi and Mac rmapi pins should bump in lockstep
  - Add a one-line maintainer comment near the rmapi version block of the Pi bootstrap script pointing readers at the macOS bootstrap script for the corresponding pin (no behavior change to the Pi script)
  - Observable: a maintainer reading either bootstrap script sees the cross-reference; the scripts README documents both pins side by side and includes the macOS bump procedure and the quarantine-clear caveat
  - _Requirements: 1.2, 1.3_
  - _Boundary: scripts/README.md and scripts/install-pi.sh_

- [ ] 5. Validation
- [ ] 5.1 End-to-end verification on macOS Apple Silicon hardware
  - Run the macOS bootstrap on a clean Apple Silicon Mac and confirm it succeeds; immediately re-run it and confirm idempotency (no redownload, no venv recreation, exit 0)
  - Run renewsable pair, complete the reMarkable cloud pairing flow with a real one-time code, and confirm a non-empty token file is written at the user's rmapi configuration path
  - Run renewsable test-pipeline against the example config and confirm a dated EPUB appears both in the configured output directory and in the configured reMarkable folder
  - Run renewsable install-schedule and renewsable uninstall-schedule on the Mac; confirm both exit non-zero with a message that names the manual entrypoints, and that no LaunchAgents plist, no launchctl invocation, and no other host-scheduler state is touched
  - Observable: all four checks succeed on real hardware; any failure is a hard stop before merge
  - _Requirements: 1.1, 1.5, 2.1, 2.2, 3.1, 3.2, 3.3_

- [ ] 5.2 Pi non-regression verification
  - On a Raspberry Pi running Pi OS Bookworm 64-bit (or the existing production Pi), check out this branch and run pip install -e . inside the existing venv
  - Run renewsable install-schedule and confirm the systemd user timer is installed and fires at the configured schedule_time; subsequently run renewsable uninstall-schedule and confirm the timer is removed
  - Run renewsable run and confirm a dated EPUB is built locally and uploaded to the configured reMarkable folder
  - Run the full pytest suite on the Pi (or in a Linux container that matches the Pi runtime) and confirm zero failures
  - Observable: the Pi deployment behaves identically to before this spec, the daily timer continues to work end-to-end, and pytest reports zero failures
  - _Requirements: 5.1, 5.2, 5.3, 5.4_
