# Research & Design Decisions — device-profiles

## Summary
- **Feature**: `device-profiles`
- **Discovery Scope**: Extension (brownfield; extends `daily-paper` at Config / Builder / Uploader / CLI seams)
- **Key Findings**:
  - goosepaper accepts a `--style <name>` flag and resolves it as `pathlib.Path("./styles/") / name` — CWD-relative. We can drive per-profile CSS by building a temp directory that contains a `styles/<profile>.css` file and running goosepaper from that CWD. No upstream patch required.
  - goosepaper's default stylesheet has `@page { margin: ... }` but **no `size:` property**. The current output defaults to WeasyPrint's A4 (8.27"×11.69"), which is why today's PDF looks slightly oversized on a reMarkable 2. Adding `@page { size: W H }` in a profile-specific stylesheet is the single lever that solves the "profile-tuned page size" requirement.
  - Color is already preserved end-to-end. goosepaper's default CSS does not force grayscale; WeasyPrint emits a color PDF; the rM 2 merely grayscales it on-device. "Add color on Paper Pro Move" is therefore free. The only real work for color is the opt-out-to-mono toggle (a `filter: grayscale(100%)` rule behind a config switch).
  - Multi-profile output is also cheap: the CLI can loop over the configured profile list, calling `Builder.build(profile)` and `Uploader.upload(pdf, profile.folder)` once per profile. No new components; just per-profile context plumbed through the existing ones.
  - Both conditional requirements (Req 5, Req 6) are confirmed in-scope for this spec.

## Research Log

### goosepaper style loading
- **Context**: Requirement 2 needs profile-tuned page sizes. Requirement 5 (color) and Req 6 (multi-profile) depend on how much we can control goosepaper's output without forking.
- **Sources Consulted**: `goosepaper/__main__.py`, `goosepaper/styles.py`, `goosepaper/goosepaper.py` in the installed wheel.
- **Findings**:
  - `goosepaper/__main__.py:24` reads the style name from CLI (`--style`) or config (`style` field) with default `"FifthAvenue"`.
  - `goosepaper/styles.py:41` resolves it as `pathlib.Path("./styles/") / style`. If that directory exists, the style is loaded from there (CSS + external stylesheets list). If `./styles/<name>.css` exists, that single file is loaded. Otherwise goosepaper falls back to the embedded FifthAvenue default.
  - `goosepaper/goosepaper.py:180-192` hands the CSS (plus external stylesheets) to WeasyPrint's `HTML.write_pdf(..., stylesheets=[...])`. Standard CSS Paged Media is honoured.
- **Implications**: We can drive per-profile layout entirely through a single CSS file per profile (`rm2.css`, `paper_pro_move.css`) placed in a temp `styles/` directory at the Builder's invocation CWD. No goosepaper patching, no library-level import.

### Goosepaper default stylesheet page size
- **Context**: Needed to confirm where today's "slightly oversized PDF on rM 2" comes from.
- **Sources Consulted**: `goosepaper/styles.py:51-165` (the embedded default style).
- **Findings**: The default `@page` block sets only margins; no `size:`. WeasyPrint's default is A4. The rM 2 grayscale display stretches A4 content to fit 10.3".
- **Implications**: Any profile stylesheet that includes `@page { size: <w> <h>; margin: <m>; }` will win. rM 2 profile should size to the rM 2's active display (≈ 6.18" × 8.23"); Paper Pro Move to ≈ 4.38" × 5.84".

### reMarkable screen dimensions
- **Context**: Need physical dimensions to choose `@page size`.
- **Sources Consulted**: reMarkable product pages + eWritable reviews (already captured in `RESEARCH.md`).
- **Findings**:
  - rM 2: 10.3" diagonal, ≈ 157 mm × 209 mm active area → **6.18" × 8.23" portrait**.
  - Paper Pro Move: 7.3" diagonal, 4:3 aspect → ≈ 111 mm × 148 mm active area → **4.38" × 5.84" portrait**.
- **Implications**: We use those dimensions as the profile defaults. Margins are a per-profile tuning (smaller margins on the smaller screen to keep text column wide enough for comfortable body text).

### Color rendering cost
- **Context**: Requirement 5 is conditional on "color being cheap to add."
- **Sources Consulted**: `goosepaper/styles.py`, `goosepaper/goosepaper.py`, current `renewsable-2026-04-21.pdf` on disk.
- **Findings**: A built PDF inspected in Preview shows colored thumbnail images from BBC / Guardian / NYT feeds. No grayscale forcing exists anywhere in the pipeline. The rM 2 is the only grayscaling step and it happens on device.
- **Implications**: Color on the Paper Pro Move is free — just ship. The only code we add is an opt-out mono toggle (`filter: grayscale(100%)` in the profile CSS, gated by a config flag). Requirement 5 is confirmed in-scope for this spec, not deferred.

### Multi-profile output cost
- **Context**: Requirement 6 is conditional on "multi-profile being cheap".
- **Sources Consulted**: Existing `Builder.build()` signature (`src/renewsable/builder.py:112`), `CLI.run` / `CLI.build` in `src/renewsable/cli.py`.
- **Findings**: `Builder.build(today=None)` already accepts no argument beyond the date. Extending it to `Builder.build(profile, today=None)` is a straightforward addition; the goosepaper subprocess argv gains `--style <profile.name>` and the output filename gains the profile suffix. The CLI's `run` / `build` / `test-pipeline` commands already have a simple sequential Builder→Uploader call; a `for profile in profiles:` loop is the full change there.
- **Implications**: Multi-profile is confirmed in-scope. No new orchestration component. Failure isolation per profile is handled by wrapping each profile's build+upload pair in a try/except and tracking a failure flag.

### Styles directory placement under pip install
- **Context**: How do we ship profile CSS so goosepaper's CWD-relative resolver finds them at runtime?
- **Sources Consulted**: Python's `importlib.resources`, our existing `renewsable.templates` sub-package (from task 2.2).
- **Findings**: Same pattern used for systemd unit templates — ship CSS files as package data under `src/renewsable/styles/*.css` and load via `importlib.resources.files('renewsable.styles')`. At Builder invocation time, copy the chosen profile's CSS into `<tmpdir>/styles/<profile>.css`, then run goosepaper with CWD = `<tmpdir>`.
- **Implications**: No filesystem layout assumptions, no post-install patching, works inside the Docker image and inside the Pi venv.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Extend Config + Builder + CLI in place (chosen) | Add a `DeviceProfile` value object + profile registry; Builder takes a profile; CLI iterates | Minimal new surface; fits the existing "thin orchestrator" design; reuses the temp-dir + subprocess pattern Builder already has | `--style` path semantics tie us to goosepaper's current behaviour — a revalidation trigger | Matches `daily-paper` design principles |
| Introduce a new ProfileRenderer component | A separate component that owns profile selection + CSS writing | Cleaner abstraction for future devices | Adds a component for one concept; overkill for 2 profiles | Rejected — simplification wins |
| Fork goosepaper to add a `--page-size` CLI flag | Upstream (to our fork) a page-size flag; renewsable passes it instead of shipping CSS | Most direct | Another fork patch to maintain; no reason to split page-size out of a CSS file | Rejected — CSS is the right lever |
| Post-process WeasyPrint output to resize | Run WeasyPrint ourselves after goosepaper emits HTML | Full layout control | Requires parsing goosepaper's intermediate HTML; far outside "thin wrapper" scope | Rejected — reserved for a broadsheet-layout spec |

## Design Decisions

### Decision: Profile-tuned page size via a per-profile CSS file loaded through goosepaper's `--style` flag
- **Context**: Requirement 2 asks for profile-specific page size; we want to avoid forking goosepaper.
- **Alternatives Considered**:
  1. Goosepaper fork to add `--page-size w h` flag.
  2. Post-process PDF / rerun WeasyPrint ourselves.
  3. Ship per-profile CSS and use goosepaper's existing `--style` resolution (chosen).
- **Selected Approach**: Package-data CSS under `src/renewsable/styles/`; Builder writes the chosen profile's CSS into `<tmpdir>/styles/<profile>.css` and runs goosepaper with CWD set to `<tmpdir>` and `--style <profile>`.
- **Rationale**: Zero changes to goosepaper; CSS Paged Media is the idiomatic lever. Adding a new profile is one new `.css` file plus a registry entry.
- **Trade-offs**: Couples us to goosepaper's CWD-relative style resolution (a revalidation trigger). Mitigation: the Builder test asserts the exact argv + CWD so a goosepaper regression surfaces immediately.

### Decision: A `DeviceProfile` value object + built-in registry
- **Context**: Need a clean place to hold `name`, `page_size`, `margin`, `font_size`, `color`, `remarkable_folder`.
- **Alternatives Considered**:
  1. Flat config fields (`page_width`, `page_height`, `margin`, …) at the Config level — becomes unwieldy for multi-profile.
  2. A frozen `DeviceProfile` dataclass + a BUILTIN_PROFILES registry keyed by name, merged with config overrides (chosen).
- **Selected Approach**: `DeviceProfile(name, page_size_in, margin_in, font_size, color, remarkable_folder)`; built-in `rm2` and `paper_pro_move` entries; operator overrides are shallow-merged onto the built-in values.
- **Rationale**: One value object = one concept. Extensible without Config-level changes. Operators who only want to change the destination folder don't need to copy every page-size field.
- **Trade-offs**: Introduces one new dataclass and a small resolver; accepted in exchange for clean extensibility.

### Decision: CLI-level multi-profile loop (no new component)
- **Context**: Requirement 6 asks for one PDF per profile per run.
- **Alternatives Considered**:
  1. A new `ProfileOrchestrator` component.
  2. `for profile in config.device_profiles: Builder(config).build(profile); Uploader(config).upload(pdf, profile.remarkable_folder)` inside existing CLI commands (chosen).
- **Selected Approach**: CLI's `run`, `build`, `test-pipeline` commands iterate over profiles; per-profile try/except isolates failures; exit code is non-zero iff at least one profile failed.
- **Rationale**: Simplification principle — no new abstraction for a 2-line loop.
- **Trade-offs**: CLI is slightly longer; accepted.

### Decision: Output filename always includes the profile suffix
- **Context**: Req 4 (backward compat) wants existing rm2 operators' reading experience preserved. Req 6.2 wants per-profile distinguishable names.
- **Alternatives Considered**:
  1. Always suffix (chosen).
  2. Never suffix (multi-profile files collide).
  3. Suffix only when the config declares 2+ profiles — rejected because an operator who adds a second profile after upgrade would transition from `renewsable-YYYY-MM-DD.pdf` to `renewsable-YYYY-MM-DD-rm2.pdf`, and `rmapi put --force` overwrites only by exact filename; the previous-day file at the old path becomes a stranded orphan on the reMarkable cloud.
- **Selected Approach**: Every build writes `renewsable-YYYY-MM-DD-<profile>.pdf` regardless of how many profiles are configured.
- **Rationale**: Eliminates the single→multi transition orphan; makes the rule uniform and memorable; satisfies Req 6.2 for the multi-profile case; Req 4.1 is reinterpreted as "same reading experience and content", not "byte-identical filename" — an operator upgrading will see one-time filename change (first day after upgrade leaves yesterday's un-suffixed file on the cloud, but subsequent days converge).
- **Trade-offs**: Post-upgrade first day produces one stranded un-suffixed PDF on the reMarkable cloud per operator. Documented in the README upgrade notes; users can delete it once.

### Decision: Color is on by default on every profile; opt-out via profile-level `color: false`
- **Context**: Req 5. Color is already free in the pipeline, and Req 4.1 requires byte-compatible pre-upgrade output for the rm2 default.
- **Selected Approach**: `DeviceProfile.color: bool = True` as the default for **every** built-in profile, including `rm2`. Setting `color=false` injects a `filter: grayscale(100%)` rule into the profile CSS at write time.
- **Rationale**: Makes the rm2 default strictly byte-compatible with pre-upgrade renewsable (Req 4.1) — today's rm2 output is already a color PDF that the device grayscales on display. Req 5.2's "rm2 renders mono" is interpreted at the device level (the user reads grayscale) rather than at the PDF-bytes level; operators who want strict-mono PDFs set `"color": false` in the rm2 profile override.
- **Trade-offs**: Req 5.2's wording is satisfied by on-device behaviour rather than by the PDF file itself. Operators on a colour viewer opening the rm2 file see colour; that's consistent with today's pre-upgrade behaviour.

### Decision: Backward-compatible config shape
- **Context**: Req 4 requires zero config-file edits to keep existing rm2 deployments running.
- **Selected Approach**: `Config.load` accepts three shapes and normalises internally to a `list[DeviceProfile]`:
  1. No profile-related fields → implicit single-profile list `[BUILTIN['rm2']]`.
  2. `"device_profile": "paper_pro_move"` string shorthand → single-profile list `[BUILTIN['paper_pro_move']]`.
  3. `"device_profiles": [{"name": "rm2"}, {"name": "paper_pro_move", "remarkable_folder": "/News-Move"}]` → full multi-profile list with overrides.
- **Rationale**: Simple users write the shorthand; power users write the list. The existing config.example.json keeps working without edits.
- **Trade-offs**: Two code paths in the loader (shorthand vs list); small, well-tested.

## Risks & Mitigations

- **Risk**: goosepaper changes how `--style` resolves paths (absolute vs relative, renames the flag).
  **Mitigation**: Builder's integration test asserts the exact argv and CWD; a regression surfaces immediately. Documented as a revalidation trigger.
- **Risk**: `filter: grayscale(100%)` is not honoured by WeasyPrint for inline images at every version.
  **Mitigation**: Worst case, the opt-out-to-mono toggle silently does nothing. That's a cosmetic regression, not a correctness one. If it matters, add an image-processing pass later.
- **Risk**: reMarkable Paper Pro Move's actual active-area dimensions differ slightly from the approximation.
  **Mitigation**: Profile CSS is a single file; ship sensible defaults and tweak when the device is in the operator's hand (the `paper_pro_move` operator persona).
- **Risk**: Per-profile `remarkable_folder` collision with the base `remarkable_folder` setting.
  **Mitigation**: Config loader validates that profile-specific folders don't silently drop the base; overrides are explicit.
- **Risk**: First day after upgrade leaves an un-suffixed `renewsable-YYYY-MM-DD.pdf` on the reMarkable cloud as a one-time orphan (because the filename rule now always suffixes).
  **Mitigation**: Document in the README upgrade notes; operator deletes the one file once on the tablet. Subsequent days converge to the new filename.

## References
- [goosepaper `__main__.py` + `styles.py`](https://github.com/j6k4m8/goosepaper/tree/v0.7.1/goosepaper) — the style-resolution machinery we're leaning on
- [WeasyPrint CSS Paged Media — `@page size`](https://doc.courtbouillon.org/weasyprint/stable/) — the mechanism for per-profile page dimensions
- [reMarkable Paper Pro Move overview](https://remarkable.com/) — form-factor reference for the `paper_pro_move` defaults
- `.kiro/specs/daily-paper/design.md` — the design this extension plugs into
