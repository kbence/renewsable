# Brief: daily-paper

## Problem
The user wants to read a curated daily news digest on their reMarkable 2 tablet each morning without manually gathering, formatting, or transferring files. Reading news on phone/laptop is distracting; reading it on e-ink is focused, but the device has no native "morning paper" mechanism.

## Current State
- Greenfield project. Only `RESEARCH.md` and scaffolding files exist in the working directory.
- The user develops on macOS (darwin) but owns a Raspberry Pi on the home LAN that will run the scheduled job. Pi assumed to run Raspberry Pi OS (Debian-based, systemd, always-on).
- User owns a reMarkable 2 (10.3", monochrome) and has a reMarkable cloud account available for pairing via one-time code.
- No feeds, no auth tokens, no running pipeline.

## Desired Outcome
Each morning, a dated PDF (e.g. `renewsable-2026-04-19.pdf`) appears in a `/News/` folder on the user's reMarkable 2, containing the day's stories from a configurable list of feeds. The pipeline runs on an always-on Raspberry Pi at a configurable local time (default 05:30), so sleep/wake reliability is not a concern.

## Approach
**Hybrid Option A/B from RESEARCH.md — build on `goosepaper`, deployed to a Raspberry Pi.**

Pipeline: `goosepaper` (RSS → WeasyPrint PDF) → `rmapi` (ddvk fork, ARM Linux build) → **`systemd` timer + service unit** on Raspberry Pi OS. A thin wrapper script owns the feed config, invocation, and upload. A YAML or JSON config file drives the feed list, output folder, and schedule time. The schedule time is read by a small helper that renders the systemd timer unit, so changing time = edit config + `renewsable install-schedule` to regenerate and reload.

Deployment is Pi-side: clone the repo (or rsync from the Mac), install Python deps into a venv, install the `rmapi` ARM binary, pair once interactively via SSH, then install the systemd user units. The Mac is the dev box only; it does not run the schedule.

This gets a working daily paper on the device within day one, gains "paper always ready at wake-up" reliability from the always-on Pi, and defers custom layout work until the feed list and reading habits stabilize.

## Scope
- **In**:
  - Wrapper around `goosepaper` that produces a dated PDF from a configurable feed list
  - Feed list includes English-language defaults (BBC, NYT, Guardian, Economist, FT, Ars Technica, Hacker News) plus **telex.hu** as the first Hungarian source
  - Upload step via `rmapi put` into a configurable reMarkable folder (default `/News/`)
  - Configurable schedule time via a config file and/or CLI flag; generates the systemd timer unit from that config
  - Raspberry Pi (ARM Linux, systemd) as the runtime host; user-scoped systemd units (`~/.config/systemd/user/`) with `loginctl enable-linger` for persistence across reboots
  - First-run setup (run on the Pi over SSH): install system deps for WeasyPrint (Cairo/Pango/GDK-Pixbuf), create venv, install Python deps, fetch the `rmapi` ARM binary, pair `rmapi` interactively, install systemd units, dry-run command
  - Deployment workflow from the Mac: `git` clone/pull on the Pi, or `rsync` + remote install command (pick one; document it)
  - Basic logging captured via `journalctl --user -u renewsable.service`, plus a plain-text log file under the project's state dir for easy tailing
- **Out**:
  - Custom broadsheet CSS layout (defer — use goosepaper defaults first)
  - Deduplication across feeds beyond what goosepaper already does
  - SQLite "seen" store (defer — goosepaper re-runs produce a fresh paper each day, acceptable for MVP)
  - Paywall circumvention of any kind
  - macOS `launchd` scheduling (Mac is dev box only; explicitly out of scope)
  - GitHub Actions scheduling (documented as an alternative, not implemented)
  - Color tuning for Paper Pro (user has rM 2)
  - EPUB output
  - Web UI or monitoring dashboard
  - Multi-user support
  - Provisioning the Pi itself (OS install, SSH setup, Wi-Fi) — assumed already done

## Boundary Candidates
- **Configuration** — feed list, output folder, schedule time, reMarkable target folder. One file, one schema.
- **Content build** — invoke goosepaper (or fall back to direct feedparser/trafilatura if telex.hu needs it) to produce today's PDF.
- **Upload** — thin shell over `rmapi put`, idempotent re-uploads with `--force`.
- **Scheduling** — launchd plist generation + install/uninstall commands.
- **CLI / entrypoint** — `renewsable build`, `renewsable upload`, `renewsable run` (build+upload), `renewsable install-schedule` (renders + loads the systemd timer), `renewsable pair-remarkable`.

## Out of Boundary
- Replacing goosepaper's rendering — MVP uses its defaults; forking happens in a later spec if needed.
- Any cloud-hosted runner — self-hosted on the user's Pi only for this spec.
- Running the pipeline on the Mac — dev box only.
- News source discovery / recommendation — feeds are user-maintained.
- Cross-device sync beyond reMarkable cloud.

## Upstream / Downstream
- **Upstream**:
  - `goosepaper` (Python, WeasyPrint-based) — the rendering engine
  - `rmapi` (ddvk fork, **Linux ARM** binary from the ddvk/rmapi releases page) — upload client
  - `feedparser` / `trafilatura` — available if telex.hu or another Hungarian source needs a custom provider beyond goosepaper's RSS support
  - Raspberry Pi OS userland: `systemd` (user services + timers), `journalctl`, `loginctl enable-linger`
  - System libs required by WeasyPrint on Debian/Raspberry Pi OS: `libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz0b`, `libcairo2`, `libgdk-pixbuf-2.0-0` (exact list confirmed during implementation)
- **Downstream** (future specs, not in this spec):
  - Custom broadsheet layout spec (WeasyPrint CSS Paged Media)
  - Deduplication + "seen" store spec
  - Optional GitHub Actions delivery spec for when the Mac is off

## Existing Spec Touchpoints
- **Extends**: none — first spec in the project.
- **Adjacent**: none.

## Constraints
- **Runtime platform**: Raspberry Pi running Raspberry Pi OS (Debian-based, systemd, ARM — likely aarch64 on Pi 4/5, armv7 on Pi 3). Shell is bash. Always-on; no sleep/wake handling needed.
- **Dev platform**: macOS (darwin). Dev loop edits on Mac, deploys to Pi over SSH. The code must not depend on macOS-only facilities.
- **Python version**: whatever ships with current Raspberry Pi OS (Python 3.11+ on Bookworm). Use a venv; do not rely on system-site-packages.
- **Device**: reMarkable 2 — monochrome, portrait, ~10.3". Page size and typography choices must be legible on that screen; design for mono (no color dependence).
- **reMarkable auth**: no public API; auth requires a one-time code from `my.remarkable.com/device/desktop/connect`. Pairing happens once on the Pi over SSH. Token lives in `~/.config/rmapi/rmapi.conf` on the Pi and must be backed up.
- **Hungarian source (telex.hu)**: verify telex.hu exposes a usable RSS feed and that goosepaper handles non-English content (Unicode, quotation marks, hyphenation) before committing to "just use goosepaper". If goosepaper's built-in RSS provider falls short, add a thin custom provider using `feedparser` + `trafilatura`. Ensure `hu_HU` locale and appropriate fonts are available on the Pi.
- **Time-of-day configurability**: schedule time must be editable via config file or CLI, not hardcoded in the timer unit. Regenerate and `systemctl --user daemon-reload && systemctl --user restart renewsable.timer` on change.
- **Network**: the Pi must have outbound internet at schedule time to fetch feeds and upload to rM cloud. If offline, retry with backoff; do not fail silently.
- **Low maintenance**: solo developer — prefer leveraging existing tools over custom code wherever the quality trade-off is acceptable.
- **Ethics**: respect robots.txt and ToS, honest User-Agent, no paywall bypass.
