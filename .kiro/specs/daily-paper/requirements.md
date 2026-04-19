# Requirements Document

## Introduction

**Who has the problem.** A solo developer who develops on macOS, owns a reMarkable 2, and has an always-on Raspberry Pi on their home LAN. They want to read a curated morning news digest on e-ink instead of on a phone or laptop.

**Current situation.** No pipeline exists. The reMarkable has no native "morning paper" mechanism, and manually gathering, formatting, and transferring news each day is friction the user will not sustain. The working directory is greenfield apart from `RESEARCH.md` and Kiro scaffolding.

**What should change.** A scheduled daily job, running on the user's Raspberry Pi (Raspberry Pi OS, systemd), produces a dated PDF from a configurable list of RSS feeds (English defaults plus at least telex.hu as a first Hungarian source) and uploads it to a configurable folder on the user's reMarkable 2 via `rmapi`. The approach builds on `goosepaper` as the rendering engine (WeasyPrint under the hood), wraps it with a thin CLI and config file, and schedules runs through a **systemd user timer** on the Pi whose fire time is driven by config (default 05:30 local) rather than hardcoded. The Mac is the development box only; deployment is via SSH/git/rsync to the Pi. The first implementation targets the reMarkable 2 (monochrome, portrait) and deliberately defers custom broadsheet layout, deduplication stores, EPUB output, and cloud-hosted scheduling to future specs.

See `.kiro/specs/daily-paper/brief.md` for the full discovery context.

## Boundary Context

- **In scope**:
  - Fetching stories from a user-maintained list of RSS feeds, including English and Hungarian sources (telex.hu is the named Hungarian source for the first implementation).
  - Rendering a single dated PDF per day sized and styled for the reMarkable 2 (monochrome, portrait).
  - Uploading the PDF to a configurable folder on the user's reMarkable cloud account.
  - Running the pipeline unattended on the Pi at a configurable local time that defaults to 05:30.
  - A CLI on the Pi for manual build, manual upload, combined run, initial reMarkable pairing, and installing/updating the schedule.
  - First-run setup and deployment workflow from the macOS dev box to the Pi.
  - Operator-observable logging and error signalling.
- **Out of scope**:
  - Custom broadsheet CSS layout beyond what the chosen renderer provides out of the box.
  - Cross-day deduplication or a persistent "seen stories" store.
  - EPUB output.
  - Running the pipeline on macOS, on a cloud runner, or on the reMarkable device itself.
  - Paywall circumvention, bypassing `robots.txt`, or anonymising requests.
  - Provisioning the Pi (OS install, SSH, Wi-Fi) or the reMarkable (account creation, Wi-Fi).
  - Multi-user support, web UI, monitoring dashboard, color tuning for Paper Pro.
- **Adjacent expectations**:
  - The reMarkable cloud service is available and accepts uploads via a paired device token obtained from `my.remarkable.com/device/desktop/connect`. This feature does not own the reMarkable cloud protocol and relies on an existing community client for upload.
  - The Pi has outbound internet connectivity at the scheduled time, an accurate system clock/timezone, and is kept powered on by the user.
  - Per-site content policies, `robots.txt`, and feed terms of service are expected to be respected; this feature does not bypass them.
  - Future specs may replace the rendering engine, add a "seen stories" store, or move scheduling to a cloud runner. Those extensions are explicitly adjacent and not owned here.

## Requirements

### Requirement 1: Configuration-driven feed list and output settings
**Objective:** As the user, I want feeds, output folder names, schedule time, and reMarkable destination folder to live in a single human-editable config file on the Pi, so that I can change what the paper contains and when it runs without editing code.

#### Acceptance Criteria
1. The renewsable system shall read its runtime settings from a single configuration file whose location is either a documented default path on the Pi or an explicit `--config` CLI argument.
2. The renewsable system shall allow the configuration file to specify, at minimum: the list of news sources, the reMarkable destination folder, the local schedule time, and the local filesystem directory where built PDFs are written.
3. When the configuration file is missing, the renewsable system shall fail the current command with an error message that names the expected path and the missing field, and shall not produce or upload a PDF.
4. When the configuration file is present but a required field is missing or malformed, the renewsable system shall fail the current command with an error message that names the field and the problem, and shall not produce or upload a PDF.
5. When the configuration file is edited, the renewsable system shall use the new values on the next invocation without requiring a reinstall of the system.
6. Where the configuration file does not set an optional field, the renewsable system shall fall back to a documented default.

### Requirement 2: Daily news digest build
**Objective:** As the user, I want a single dated PDF per day containing the latest stories from my configured feeds, so that I have one artifact to open on the tablet.

#### Acceptance Criteria
1. When the user runs the build command, the renewsable system shall produce exactly one PDF file named with the current local date (for example `renewsable-2026-04-19.pdf`) in the configured output directory.
2. The renewsable system shall include content from every feed listed in the configuration that responded successfully during the run.
3. When a build on a given date is run more than once, the renewsable system shall overwrite or replace the PDF for that date rather than create a second file.
4. The renewsable system shall size and orient the PDF for legibility on a 10.3" monochrome portrait e-reader.
5. The renewsable system shall include, at the top of the document or its first page, the issue date in human-readable form.
6. If the build produces no stories at all (every configured feed failed or returned nothing), the renewsable system shall exit with a non-zero status and shall not upload an empty PDF.

### Requirement 3: Hungarian-language source support
**Objective:** As the user, I want Hungarian sources (telex.hu to start) to render correctly with proper accented characters, so that I can read Hungarian news on the tablet without garbled text.

#### Acceptance Criteria
1. The renewsable system shall accept `telex.hu` as a configured source and include its stories in the daily PDF when the feed responds successfully.
2. The renewsable system shall render Hungarian accented characters (including `á é í ó ö ő ú ü ű` and their uppercase forms) correctly in the output PDF, with no replacement glyphs, mojibake, or missing-character boxes.
3. When a configured Hungarian source is unreachable or returns an error, the renewsable system shall continue building the PDF with the remaining sources and shall record the failure in the run's logs.
4. Where the first implementation's default renderer cannot extract usable article content from telex.hu, the renewsable system shall still include at least each available story's title, source, and link in the output PDF.

### Requirement 4: Upload to the user's reMarkable
**Objective:** As the user, I want each day's PDF to appear automatically in a chosen folder on my reMarkable 2, so that I can pick up the tablet and read without any manual transfer.

#### Acceptance Criteria
1. When the user runs the combined build-and-upload command and the build succeeds, the renewsable system shall upload the resulting PDF to the reMarkable folder named in configuration.
2. When the configured reMarkable destination folder does not yet exist on the user's reMarkable cloud, the renewsable system shall create it before uploading.
3. When a PDF for the same date has previously been uploaded and a new upload is requested, the renewsable system shall replace the prior day's file (or produce a deterministic updated version), and shall not leave multiple duplicates of the same date in the destination folder.
4. If the reMarkable cloud rejects the upload or the network is unavailable, the renewsable system shall exit with a non-zero status, report the error in logs, and leave the locally built PDF in the output directory so it can be re-uploaded later.
5. The renewsable system shall not upload any file if the preceding build step did not complete successfully.

### Requirement 5: Scheduled unattended execution on the Raspberry Pi
**Objective:** As the user, I want the Pi to run the build-and-upload pipeline automatically each morning at a time I control, so that a fresh paper is waiting when I pick up the tablet.

#### Acceptance Criteria
1. The renewsable system shall provide a command that installs a scheduled job on the Pi which runs the combined build-and-upload pipeline once per day at the local time specified in configuration.
2. When the configured schedule time is edited and the install command is re-run, the renewsable system shall update the active schedule so that subsequent runs fire at the new time.
3. While the Pi is powered on, the renewsable system shall run the scheduled job at the configured time without requiring a user to be logged in interactively on the Pi.
4. When the Pi was powered off at the configured time and powers on later, the renewsable system shall run the next scheduled occurrence at the following day's configured time (catching up on missed runs is not required).
5. The renewsable system shall provide a command that removes the installed schedule, leaving the Pi with no further automatic runs until the schedule is reinstalled.
6. While a scheduled run is in progress, the renewsable system shall not start a second concurrent run of the same job.

### Requirement 6: First-run setup and reMarkable pairing
**Objective:** As the user, I want a guided first-run on the Pi that installs dependencies and pairs the reMarkable account once, so that setup is a finite, documented procedure rather than trial and error.

#### Acceptance Criteria
1. The renewsable system shall document, in the project README or equivalent, the ordered steps to set up a fresh Pi: install OS-level prerequisites, create a project venv, install Python dependencies, install the reMarkable upload client, pair the reMarkable account, install the schedule.
2. When the user runs the reMarkable pairing command on the Pi, the renewsable system shall prompt the user for the one-time 8-character code from `my.remarkable.com/device/desktop/connect`, complete the pairing, and persist the resulting device token in a documented location on the Pi.
3. After successful pairing, the renewsable system shall allow subsequent build-and-upload runs to complete without any further interactive prompt, until the token is revoked.
4. If the persisted device token is missing or rejected at upload time, the renewsable system shall exit with an error that explains how to re-run the pairing command.
5. The renewsable system shall provide a dry-run or "test" command that exercises the full pipeline (build + upload) on demand, so that the user can verify the setup without waiting for the next scheduled fire time.

### Requirement 7: Deployment from the dev machine to the Pi
**Objective:** As the user, I want a repeatable way to push code changes from my Mac to the Pi, so that I can iterate without hand-copying files.

#### Acceptance Criteria
1. The renewsable system shall document a single supported deployment workflow from the macOS dev machine to the Pi (for example, `git pull` on the Pi, or `rsync` from the Mac — one chosen and documented).
2. When the user follows the documented deployment workflow, the renewsable system shall be runnable on the Pi without requiring any macOS-only tools at runtime.
3. When Python dependencies change, the documented deployment workflow shall include the step required to refresh them on the Pi.
4. When configuration changes that affect the schedule, the documented deployment workflow shall include the step required to regenerate and reload the scheduled job.

### Requirement 8: Observability and logging
**Objective:** As the user/operator, I want to see what the scheduled runs did and why they failed, so that I can diagnose issues without adding a monitoring stack.

#### Acceptance Criteria
1. The renewsable system shall record, for each run, a log entry containing at minimum: the run's start timestamp, the list of sources attempted, each source's success or failure, the output PDF path, and the upload outcome.
2. The renewsable system shall make the scheduled-run logs visible both via the Pi's standard system log facility (queryable without special tooling) and as a plain-text log file under a documented path, retaining at least the last 14 days of runs.
3. When a source fails, the renewsable system shall log a message that identifies the source and the reason for the failure.
4. When the upload step fails, the renewsable system shall log a message that identifies the destination folder, the local PDF path, and the reason for the failure.
5. The renewsable system shall not log the reMarkable device token, one-time code, or any other credential in plain text.

### Requirement 9: Error handling and resilience
**Objective:** As the user, I want transient failures (one feed down, brief network hiccup) to not destroy the day's paper, so that one flaky source doesn't cost me the digest.

#### Acceptance Criteria
1. If a single configured feed is unreachable, times out, or returns malformed content, the renewsable system shall skip that feed, record the failure, and continue building the PDF from the remaining feeds.
2. If feed fetches fail transiently, the renewsable system shall retry each failing feed a small, bounded number of times with backoff before giving up on it for the run.
3. If the reMarkable upload fails transiently, the renewsable system shall retry the upload a small, bounded number of times before exiting with failure.
4. While fetching any source, the renewsable system shall send a User-Agent string that identifies the tool, and shall honour the source's `robots.txt` rules.
5. The renewsable system shall not crash, hang indefinitely, or leave a partial upload on the reMarkable cloud if any single source or the upload step fails.

### Requirement 10: Command-line surface
**Objective:** As the user, I want a small, predictable set of CLI commands, so that I can script or invoke the pipeline by hand without reading code.

#### Acceptance Criteria
1. The renewsable system shall expose, at minimum, these commands on the Pi: build only, upload only, combined run (build + upload), install or reinstall the schedule, remove the schedule, and pair the reMarkable account.
2. When the user runs any renewsable command with a `--help` flag, the renewsable system shall print a usage summary for that command.
3. When the user runs the upload-only command with an explicit PDF path, the renewsable system shall upload that file to the configured reMarkable destination folder without rebuilding.
4. When any renewsable command completes successfully, the renewsable system shall exit with status code zero.
5. When any renewsable command fails, the renewsable system shall exit with a non-zero status code and print a human-readable error to standard error.

