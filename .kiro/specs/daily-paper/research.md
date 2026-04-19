# Research & Design Decisions — daily-paper

## Summary
- **Feature**: `daily-paper`
- **Discovery Scope**: New Feature (greenfield) / External Integration
- **Key Findings**:
  - `goosepaper`'s built-in `--upload` depends on the legacy `rmapy` client which is broken against current reMarkable cloud auth. Upload must be driven externally via the Go `rmapi` CLI.
  - `rmapi` on Linux ships prebuilt binaries only for `linux-amd64` and `linux-arm64`. There is no prebuilt `armhf` binary, so this spec requires **64-bit Raspberry Pi OS** (aarch64). Pi 3 users must run the 64-bit image.
  - `rmapi put` is not auto-idempotent — it fails if the target filename exists and provides `--force` (drops annotations) and `--content-only` (preserves annotations) as mutually exclusive overwrite modes. For daily overwrite with no user annotation expectation, `--force` is appropriate. Destination folders must be pre-created with `rmapi mkdir`.
  - `goosepaper`'s config format is JSON with a `stories` array of `{provider, config}` entries; this spec will use goosepaper's native schema as a sub-object inside a larger renewsable config to avoid an unnecessary translation layer.
  - `telex.hu` publishes RSS at `https://telex.hu/rss`; feed items contain teasers only, so full-body extraction is required — goosepaper's built-in RSS provider already handles this via `readability-lxml`, so no custom provider is needed for the first implementation.
  - WeasyPrint 60+ on Raspberry Pi OS Bookworm no longer requires Cairo/GDK-Pixbuf at runtime; the dependency set is smaller than older docs suggest.
  - `systemd` user timers with `Persistent=true` and `loginctl enable-linger` cover all operational requirements (unattended schedule, survives reboot/relogin, catches missed runs).

## Research Log

### goosepaper (PyPI, GitHub)
- **Context**: Chosen as the rendering engine in the brief. Needed to verify config format, CLI flags, Python API for providers, Unicode handling, and upload behaviour.
- **Sources Consulted**: `https://github.com/j6k4m8/goosepaper` (README, source), PyPI listing.
- **Findings**:
  - PyPI package `goosepaper` v0.7.0 (Aug 2023) / GitHub release v0.7.1 (Feb 2024), last push Oct 2024. Lightly maintained, not abandoned.
  - Config is JSON with top-level `font_size` and `stories: [{provider, config}]`. Built-in providers: `rss`, `mastodon`, `wikipedia_current_events`, `reddit`, `weather`.
  - CLI: `goosepaper -c config.json -o output.pdf` (long forms `--config`, `--output`). Output PDF/EPUB via WeasyPrint.
  - Custom providers: subclass `goosepaper.storyprovider.StoryProvider`; Story API is `(title, body_html, byline, date)`.
  - Unicode is clean (feedparser + readability-lxml + WeasyPrint, UTF-8 end-to-end); Hungarian works provided fonts are installed.
  - Built-in `--upload` relies on `rmapy`, which is broken against current reMarkable auth — **not usable**.
- **Implications**: Adopt goosepaper for building; drive `rmapi` (Go) separately for upload. Use goosepaper's JSON `stories` schema directly inside renewsable's config.

### rmapi (ddvk fork, GitHub)
- **Context**: Chosen as the upload client. Needed to verify CLI surface, binary availability for Pi, idempotency behaviour, pairing flow.
- **Sources Consulted**: `https://github.com/ddvk/rmapi` release page and README.
- **Findings**:
  - Latest v0.0.32 (Nov 2025). Assets: `rmapi-linux-amd64.tar.gz`, `rmapi-linux-arm64.tar.gz` — no armhf.
  - Config at `~/.config/rmapi/rmapi.conf` by default; overridable via `RMAPI_CONFIG`.
  - `rmapi put file.pdf /Folder/` runs non-interactively after pairing; exit 0 / 1.
  - Default `put` fails if target exists. `--force` replaces (drops annotations/metadata). `--content-only` replaces PDF bytes, keeps annotations. Mutually exclusive.
  - `rmapi mkdir /News` works; `put` does not auto-create parent folders.
  - Pairing: run `rmapi` with no args → prints URL, prompts for 8-char one-time code from `my.remarkable.com/device/desktop/connect`, persists token in config.
- **Implications**: Target 64-bit Pi OS only. Uploader must `mkdir` (ignore "already exists") before `put --force`. Pairing is a one-time interactive setup step that must be runnable over SSH.

### systemd user timer on Raspberry Pi OS Bookworm
- **Context**: Need unattended daily schedule with configurable fire time, persistence, no user-session requirement.
- **Sources Consulted**: `systemd.timer(5)`, `systemd.time(7)` man pages; Raspberry Pi OS Bookworm is systemd-based (same as Debian 12).
- **Findings**:
  - Minimal unit pair (service `Type=oneshot` + timer `OnCalendar=*-*-* HH:MM:00`, `Persistent=true`) meets all schedule requirements.
  - `sudo loginctl enable-linger $USER` lets user units run without an active login session; required for the 05:30 run to fire when the user is not SSH'd in.
  - `journalctl --user -u renewsable.service --since today` queries logs.
  - `RandomizedDelaySec=` available if spread across runs is desired (not needed for a personal solo job).
- **Implications**: User-scoped units under `~/.config/systemd/user/`. Scheduler component renders two text files from a Jinja-like template (stdlib `string.Template` is enough) using the configured time.

### telex.hu RSS feasibility
- **Context**: Named as the first Hungarian source. Needed to verify feed availability, content completeness, any auth/geo issues.
- **Sources Consulted**: `https://telex.hu/rss` response + headers.
- **Findings**:
  - Feed exists at `https://telex.hu/rss` (200 OK, `text/xml; charset=UTF-8`). `/feed` returns 404.
  - Items contain teasers in `description`; `content:encoded` is usually empty for news items.
  - UTF-8 clean, Cloudflare-fronted, standard UA accepted, no auth, no observed geo-restriction.
- **Implications**: goosepaper's built-in `rss` provider + readability-lxml extraction is sufficient — no custom provider needed for the MVP. Hungarian fonts must be available on the Pi (dejavu + noto).

### WeasyPrint dependencies on Pi OS Bookworm
- **Context**: goosepaper uses WeasyPrint; needed to confirm Pi install story.
- **Sources Consulted**: WeasyPrint docs, Bookworm package list.
- **Findings**:
  - WeasyPrint 60+ dropped Cairo/GDK-Pixbuf runtime deps; now Pango + HarfBuzz only.
  - `apt` list: `python3-dev python3-pip python3-cffi python3-brotli libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi-dev shared-mime-info fonts-dejavu fonts-noto-core`.
  - Pure-Python + cffi; no known aarch64 issues.
  - `fonts-dejavu` covers Hungarian; add `fonts-noto-core` for wider coverage. Minimal Pi OS Lite images ship few fonts, so install explicitly.
- **Implications**: Bootstrap script installs this apt list once; scheduler and builder assume it's present.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Thin Python wrapper over external CLIs (chosen) | Python package with subprocess calls to `goosepaper` and `rmapi`; config + scheduler + CLI in Python | Smallest code surface, leverages existing tools, easy to swap later | Subprocess boundary adds error-handling complexity; tied to CLI stability of the two tools | Matches brief's "hybrid A/B" direction |
| Pure Python with goosepaper-as-library | Import `goosepaper` as a library, skip its CLI | Avoids one subprocess boundary | goosepaper's library API is less stable than its CLI and not well-documented; ties us harder to internals | Rejected |
| Shell-script-only orchestration | Bash + goosepaper CLI + rmapi + systemd | No Python code to write beyond feeds file | Config validation, error handling, and testing become painful quickly | Rejected — would grow into Python anyway |
| Rewrite rendering in-house (WeasyPrint + feedparser + trafilatura) | Skip goosepaper | Full layout control | Significantly more work; brief explicitly chose "start with goosepaper, customize later" | Rejected for MVP; reserved for a later spec |

## Design Decisions

### Decision: Drive `rmapi` externally instead of using `goosepaper --upload`
- **Context**: goosepaper ships an `--upload` flag but it binds to legacy `rmapy` which is broken.
- **Alternatives Considered**:
  1. Use `goosepaper --upload` and hope — rejected, known broken.
  2. Patch goosepaper to call `rmapi` — rejected, fork maintenance burden.
  3. Invoke `rmapi` directly from our own Uploader component after build — chosen.
- **Selected Approach**: Our CLI runs `goosepaper` to produce a PDF, then our `Uploader` shells to `rmapi mkdir` (ignoring existing-folder errors) and `rmapi put --force`.
- **Rationale**: Keeps renewsable independent of goosepaper's upload path, lets us swap either tool cleanly.
- **Trade-offs**: Two subprocess steps instead of one; slightly more code. Acceptable.
- **Follow-up**: If a future spec swaps the renderer, only `Builder` changes.

### Decision: Unified JSON config file (goosepaper schema extended)
- **Context**: goosepaper needs its own `stories` JSON; we also need schedule, output dir, reMarkable folder.
- **Alternatives Considered**:
  1. Two files (renewsable.yaml + goosepaper.json) — rejected, operationally clumsy.
  2. YAML on our side, translated to goosepaper JSON at build time — rejected, extra moving part, no user benefit.
  3. Single JSON that embeds goosepaper's `stories` array verbatim plus our top-level fields — chosen.
- **Selected Approach**: `config.json` contains `schedule_time`, `remarkable_folder`, `output_dir`, `stories` (passed through to goosepaper verbatim), and optional `font_size`. Builder writes a temp file containing only the goosepaper-relevant subset and invokes `goosepaper -c tmp.json`.
- **Rationale**: One file for the user; goosepaper stays untouched.
- **Trade-offs**: Couples our schema to goosepaper's. If goosepaper changes its schema we follow; acceptable given we pin the goosepaper version.
- **Follow-up**: Document the pass-through fields in README.

### Decision: 64-bit Raspberry Pi OS required
- **Context**: ddvk `rmapi` has no prebuilt armhf binary.
- **Alternatives Considered**: Build rmapi from source on armhf (requires Go toolchain on Pi 3) — rejected, too fiddly for MVP.
- **Selected Approach**: Prerequisites document states 64-bit Pi OS (aarch64).
- **Trade-offs**: Rules out unupgraded Pi 3. Acceptable — user confirmed Pi availability.
- **Follow-up**: Document explicitly in README.

### Decision: `--force` as the default upload mode for scheduled runs
- **Context**: Daily runs re-upload the same filename pattern; default `put` fails on conflict.
- **Alternatives Considered**:
  1. Use date-in-filename and never overwrite — rejected, accumulates clutter.
  2. `--content-only` to preserve annotations — rejected, we do not expect annotations on yesterday's paper.
  3. `--force` — chosen.
- **Selected Approach**: Uploader always passes `--force` on the scheduled path.
- **Trade-offs**: User annotations on a previous paper would be lost if an overwrite happened on the same date. In practice filenames include the date, so same-date overwrite only occurs on manual re-runs.
- **Follow-up**: Expose `--keep-annotations` CLI flag mapping to `--content-only` for manual uploads if wanted later.

### Decision: Use stdlib + a minimal CLI dep (Click); no web framework, no ORM
- **Context**: This is a single-process batch job running daily.
- **Alternatives Considered**: argparse (stdlib, verbose for subcommands), Typer (nicer DX, extra dep), Click (one common dep).
- **Selected Approach**: Click for subcommand ergonomics; stdlib `logging`, `pathlib`, `subprocess`, `json`, `string.Template`, `shutil`, `venv`, `os` for the rest.
- **Rationale**: Minimal dependency footprint on a long-lived Pi install.
- **Trade-offs**: One dep (Click).
- **Follow-up**: None.

## Risks & Mitigations

- **Risk**: `rmapi` protocol breaks when reMarkable cloud updates auth/sync (happened before with sync15).
  **Mitigation**: Pin a known-good rmapi version in the bootstrap script; document how to update; include a "last verified against" note.
- **Risk**: `goosepaper` becomes unmaintained mid-lifetime.
  **Mitigation**: Builder is a thin wrapper; swap cost is contained. Research log notes the current version.
- **Risk**: Feed sources change URLs or break (common).
  **Mitigation**: Per-feed failure tolerance (Requirement 9.1); logged clearly (Requirement 8.3); feeds easy to edit in config.
- **Risk**: Pi clock drifts and schedule fires at wrong local time.
  **Mitigation**: Rely on Raspberry Pi OS default NTP (systemd-timesyncd); note in README. Out of boundary to own this.
- **Risk**: Telex or another source rate-limits us on repeated runs.
  **Mitigation**: Identifying User-Agent (Requirement 9.4), single daily run, small footprint; goosepaper's RSS provider already batches one fetch per item. Mitigation step only needed if observed.
- **Risk**: Bootstrap script drifts from reality on future Pi OS releases.
  **Mitigation**: Version-pin the script's target (Raspberry Pi OS Bookworm, 64-bit); README calls out the supported release.

## References
- [j6k4m8/goosepaper](https://github.com/j6k4m8/goosepaper) — renderer we build on
- [ddvk/rmapi](https://github.com/ddvk/rmapi) — upload client
- [WeasyPrint docs](https://doc.courtbouillon.org/weasyprint/stable/) — PDF engine inside goosepaper
- [systemd.timer(5)](https://www.freedesktop.org/software/systemd/man/systemd.timer.html), [systemd.time(7)](https://www.freedesktop.org/software/systemd/man/systemd.time.html) — timer units and calendar syntax
- [telex.hu RSS](https://telex.hu/rss) — first Hungarian source
- [reHackable/awesome-reMarkable](https://github.com/reHackable/awesome-reMarkable) — broader tooling index
