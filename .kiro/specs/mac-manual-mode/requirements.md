# Requirements Document

## Introduction

Renewsable today supports exactly one install path: a Raspberry Pi running Pi OS Bookworm 64-bit, with a `systemd --user` timer firing the daily build-and-upload pipeline at a configured wall-clock time. This spec adds a parallel install and usage path for an Apple Silicon macOS workstation, scoped explicitly as a manual mode: the operator runs `renewsable run` themselves whenever they want a digest, and there is no scheduler equivalent. The Pi path remains the supported production path and is unchanged.

## Boundary Context

- **In scope**:
  - A macOS bootstrap script that prepares an Apple Silicon Mac (Darwin / arm64) to run renewsable end-to-end, parallel in shape to the existing Pi bootstrap.
  - Cross-platform behavior in the existing `install-schedule` and `uninstall-schedule` commands so they fail fast on macOS instead of attempting a Linux-only scheduler.
  - README documentation for the macOS manual workflow as a peer of the existing Pi runbook.
- **Out of scope**:
  - Any scheduled execution on macOS (`launchd`, `cron`, or anything else). The mode is manual by design.
  - Intel Mac (Darwin / x86_64) support. Restricted to Apple Silicon to avoid pinning a second `rmapi` artifact.
  - Any change to the build, upload, or pairing pipeline behavior. These components are already cross-platform and are reused unchanged.
  - Any change to the existing Pi bootstrap script, the systemd user timer, or `install-schedule` / `uninstall-schedule` behavior on Linux.
- **Adjacent expectations**:
  - The `renewsable pair` command is already cross-platform: it spawns `rmapi` and persists a device token at `~/.config/rmapi/rmapi.conf`. The macOS path reuses this flow as-is and does not own its persistence behavior.
  - The reMarkable cloud target folder, EPUB output filename, and configuration schema are owned by the existing `epub-output` and `daily-paper` specs and are unaffected.
  - Architecture detection in the macOS bootstrap script is symmetric to the existing Pi bootstrap: each refuses hosts the other targets.

## Requirements

### Requirement 1: macOS bootstrap install script

**Objective:** As a Mac user setting up renewsable for the first time, I want a one-command bootstrap that prepares my workstation to run the pipeline, so that I do not have to assemble the venv or download `rmapi` by hand.

#### Acceptance Criteria

1. When the operator runs the macOS bootstrap script on a Darwin / arm64 host, the macOS bootstrap script shall create a project-local Python virtual environment and install renewsable with its development dependencies in editable mode into that environment.
2. When the operator runs the macOS bootstrap script on a Darwin / arm64 host, the macOS bootstrap script shall download a pinned `ddvk/rmapi` darwin-arm64 release archive, verify the archive against an embedded SHA-256 checksum, and place an executable `rmapi` binary inside the project-local venv's `bin` directory.
3. If the downloaded `rmapi` archive's SHA-256 checksum does not match the pinned value, then the macOS bootstrap script shall abort without installing the binary and shall print a message that names the expected checksum and the offending archive.
4. If the operator runs the macOS bootstrap script on a host that is not Darwin / arm64, then the macOS bootstrap script shall exit non-zero without modifying the working directory and shall print a message that names the detected platform and states that only Apple Silicon macOS is supported.
5. When the operator re-runs the macOS bootstrap script on a host where the venv and the pinned `rmapi` binary already exist, the macOS bootstrap script shall complete successfully without re-downloading the `rmapi` archive and without recreating the venv.
6. The macOS bootstrap script shall not invoke `apt-get`, `sudo`, or any Linux package manager.

### Requirement 2: Scheduler commands fail fast on macOS

**Objective:** As a Mac user, I want `install-schedule` and `uninstall-schedule` to refuse cleanly on my machine, so that I am never led to expect a scheduled run that the host cannot deliver.

#### Acceptance Criteria

1. When the operator runs `renewsable install-schedule` on macOS, renewsable shall exit non-zero without modifying any host scheduler state and shall print a message that states scheduling is not supported on macOS and points the operator to `renewsable run` and `renewsable test-pipeline` as the manual entry points.
2. When the operator runs `renewsable uninstall-schedule` on macOS, renewsable shall exit non-zero without modifying any host scheduler state and shall print the same guidance as criterion 1.
3. While running on macOS, renewsable shall not invoke `systemctl`, `launchctl`, `crontab`, or any other host scheduler control program from the `install-schedule` or `uninstall-schedule` commands.
4. When the operator runs `renewsable install-schedule` or `renewsable uninstall-schedule` on Linux, renewsable shall continue to install or remove the systemd user timer exactly as defined by the existing `daily-paper` spec.

### Requirement 3: Pipeline parity on macOS

**Objective:** As a Mac user, I want `pair`, `build`, `upload`, `run`, and `test-pipeline` to behave the same as on the Pi, so that the manual workflow produces an equivalent daily digest on my own hardware.

#### Acceptance Criteria

1. When the operator runs `renewsable pair` on macOS after the bootstrap script has installed `rmapi`, renewsable shall complete the reMarkable pairing flow and persist the device token at the standard `rmapi` configuration path so that subsequent commands run headlessly.
2. When the operator runs `renewsable build`, `renewsable upload`, `renewsable run`, or `renewsable test-pipeline` on macOS with a valid configuration and a paired device, renewsable shall produce the same user-observable artefacts as on the Pi: a dated EPUB at the configured output directory and, for the upload-bearing commands, a corresponding upload to the configured reMarkable folder.
3. The renewsable `build`, `upload`, `run`, `pair`, and `test-pipeline` commands shall not require systemd or any host scheduler component to be present on the host.

### Requirement 4: macOS workflow documentation

**Objective:** As a Mac user reading the project documentation, I want a clearly labelled section that walks me through the manual workflow, so that I can set up and use renewsable on my Mac without inferring steps from the Pi runbook.

#### Acceptance Criteria

1. The renewsable README shall include a dedicated section that identifies the macOS manual workflow as a parallel install path to the existing Raspberry Pi runbook.
2. The macOS section in the renewsable README shall present, in order, the steps to bootstrap the install, pair with the reMarkable cloud, run an end-to-end test of the pipeline, and run the daily build manually.
3. The macOS section in the renewsable README shall state explicitly that scheduled execution is not supported on macOS and that the operator must invoke `renewsable run` themselves.
4. The renewsable README shall preserve the existing Raspberry Pi setup runbook with its current step ordering and user-observable structure.

### Requirement 5: Pi install path remains operational

**Objective:** As an existing Raspberry Pi operator, I want my deployment to keep working unchanged, so that adding macOS support does not regress my running daily paper.

#### Acceptance Criteria

1. The renewsable Raspberry Pi bootstrap script shall install renewsable, the project-local venv, and the pinned linux-arm64 `rmapi` binary on Raspberry Pi OS Bookworm 64-bit hosts.
2. When the operator runs `renewsable install-schedule` on Linux, renewsable shall install a systemd user timer that fires `renewsable run` at the configured `schedule_time`.
3. When the operator runs `renewsable uninstall-schedule` on Linux, renewsable shall remove the previously installed systemd user timer.
4. The renewsable `build`, `upload`, `run`, `pair`, and `test-pipeline` commands on Linux shall behave as defined by the existing `epub-output` and `daily-paper` specs.
