# Implementation Plan

## 1. Foundation: profiles module

- [x] 1.1 Implement `DeviceProfile`, built-in registry, and `profiles.resolve`
  - Add `src/renewsable/profiles.py` with a frozen `DeviceProfile` dataclass (`name`, `page_width_in`, `page_height_in`, `margin_in`, `font_size_pt`, `color`, `remarkable_folder`)
  - Define `BUILTIN_PROFILES` with `rm2` (6.18"×8.23", 0.35" margin, 12pt, `color=True`) and `paper_pro_move` (4.38"×5.84", 0.25" margin, 11pt, `color=True`)
  - Implement `resolve(name: str, overrides: dict | None = None) -> DeviceProfile` that looks up the built-in, shallow-merges validated overrides, and raises `ConfigError` (from `renewsable.errors`) naming the invalid value and listing supported names on unknown input
  - Validate `name` against `^[a-z][a-z0-9_]{0,31}$`; reject override entries that change `name`; reject invalid types for any field
  - Add `tests/test_profiles.py` covering: both built-ins return with documented defaults, override merging preserves unchanged fields, unknown name raises `ConfigError` with supported set in the message, override that attempts to change `name` is rejected, invalid-type override is rejected
  - Observable: `pytest tests/test_profiles.py` green; `python -c "from renewsable.profiles import BUILTIN_PROFILES; print(sorted(BUILTIN_PROFILES))"` prints `['paper_pro_move', 'rm2']`
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 3.1, 3.2, 9.1_
  - _Boundary: profiles_

- [x] 1.2 Implement `profiles.render_css` with golden-string tests
  - Add `render_css(profile: DeviceProfile) -> str` in `src/renewsable/profiles.py` emitting `@page { size: W H; margin: M; }` and `html, body { font-size: Fpt; }`
  - When `profile.color` is `False`, append `html, body { filter: grayscale(100%); }` and `img, svg { filter: grayscale(100%); }` rules
  - Extend `tests/test_profiles.py` with four golden-string cases: `render_css(rm2)`, `render_css(paper_pro_move)`, `render_css(rm2)` with `color=False` override, `render_css(paper_pro_move)` with `color=False` override; assert byte-identical output against pinned strings
  - Observable: `pytest tests/test_profiles.py::test_render_css` green across all four goldens; the CSS strings for the two built-ins each contain the exact `@page size` tuned to the design's documented dimensions
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 5.1, 5.2, 5.3_
  - _Boundary: profiles_
  - _Depends: 1.1_

## 2. Config loader extension

- [x] 2.1 Extend `Config` with `device_profiles` and loader normalisation
  - Add `device_profiles: list[DeviceProfile]` to the frozen `Config` dataclass with `field(default_factory=lambda: [BUILTIN_PROFILES["rm2"]])`
  - Extend `Config.load` / `_apply_defaults` to accept four input shapes: absent → `[rm2]`, `"device_profile": "<name>"` → `[resolve(name)]`, `"device_profile": {"name": ..., "remarkable_folder": ...}` → `[resolve(name, overrides)]`, `"device_profiles": [...]` → `[resolve(name, overrides) for each entry]`
  - Reject configs that declare both `device_profile` and `device_profiles` with a `ConfigError` naming both keys
  - Emit a DEBUG-level log entry (not WARNING) when the default profile is applied because no profile fields were declared; no log at all when profile fields were declared
  - Extend `tests/test_config.py` with four fixtures in `tests/fixtures/`: `config.no_profile.json` (existing valid fixture reused), `config.profile_string.json`, `config.profile_object.json`, `config.profiles_list.json`; assert each loads into a correct `Config.device_profiles` list
  - Observable: `pytest tests/test_config.py` green; loading the existing `config.valid.json` (no profile field) yields `Config.device_profiles == [BUILTIN_PROFILES["rm2"]]`
  - _Requirements: 1.1, 1.3, 4.1, 4.2, 4.3_
  - _Boundary: Config_
  - _Depends: 1.1_

- [x] 2.2 Config validation for profile-related errors
  - Extend `Config.validate` (or the loader's validation layer) to reject: unknown profile name, duplicate profile names within `device_profiles`, non-string profile name, non-bool color override, non-string `remarkable_folder` override, `remarkable_folder` override that does not start with `/`, `device_profiles` that is not a list, `device_profile` that is neither string nor object
  - Every raised `ConfigError` names the config file path and the offending key/value, and lists the supported profile names when relevant
  - All profile-related validation runs inside `Config.load` / `Config.validate`, before any caller gets a populated `Config` object (so feed fetch and upload never start when config is bad)
  - Extend `tests/test_config.py` with one parametrised test per failure shape (seven cases), each asserting the raised `ConfigError.message` contains both the file path and the field name
  - Observable: `pytest tests/test_config.py::test_profile_validation_errors` green across all seven cases
  - _Requirements: 1.4, 9.1, 9.2, 9.3, 9.4_
  - _Boundary: Config_
  - _Depends: 2.1_

## 3. Builder extension

- [x] 3.1 Change `Builder.build` to accept a profile and render per-profile CSS
  - Change the signature to `build(self, profile: DeviceProfile, today: datetime.date | None = None) -> Path`
  - Build the output filename as `renewsable-<YYYY-MM-DD>-<profile.name>.pdf` on every run (no conditional suffix)
  - Within the existing `TemporaryDirectory`, create `styles/` and write `profiles.render_css(profile)` to `styles/<profile.name>.css`
  - Add `--style <profile.name>` to the goosepaper argv and pass `cwd=<tmpdir>` to the subprocess call so goosepaper's CWD-relative style resolver picks up the file
  - Do not read or modify `stories[].config.limit` anywhere in the profile handling path (Req 8.1)
  - Update every existing call site of `Builder.build` in `tests/test_builder.py` to pass a profile; add new tests: goosepaper argv includes `--style <name>`, subprocess was invoked with `cwd=<tmpdir>`, `<tmpdir>/styles/<name>.css` exists with exactly the `render_css` output, output filename includes the profile suffix, two back-to-back builds for different profiles both succeed and both leave the config's `limit` values unchanged
  - Observable: `pytest tests/test_builder.py` green; `renewsable --config config/config.example.json build` on a machine with real goosepaper produces `renewsable-<today>-rm2.pdf` (suffix present even with single-profile config)
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 5.1, 5.2, 6.2, 8.1, 8.2_
  - _Boundary: Builder_
  - _Depends: 1.2_

## 4. CLI extension

- [ ] 4.1 CLI iterates over `config.device_profiles`
  - In `cli.build`, `cli.run`, and `cli.test-pipeline`: replace the single `Builder(config).build()` call with `for profile in config.device_profiles: Builder(config).build(profile)` and iterate through the existing per-command side effects (print the path in `build`, plus `Uploader(config).upload(pdf, folder=profile.remarkable_folder or config.remarkable_folder)` in `run` / `test-pipeline`)
  - Wrap each iteration in a try/except that catches `RenewsableError`, logs `logger.error("profile %s failed: %s", profile.name, exc)`, writes the error to stderr, sets a failure flag, and continues to the next profile
  - After the loop, exit 1 if any profile failed, 0 otherwise (unchanged: `ConfigError` still exits 2 via the bootstrap path)
  - `cli.upload <PATH>` remains a single-file operation (no loop); unchanged behaviour
  - Update `tests/test_cli.py`: existing single-profile run tests still pass (default-profile config continues to succeed); add tests for two-profile config (Builder+Uploader each called twice, folders correctly resolved per profile), partial-failure case (first profile's build raises, second succeeds → exit 1, both logged), explicit-upload test (`renewsable upload /tmp/x.pdf` calls Uploader once regardless of profile count)
  - Observable: `pytest tests/test_cli.py` green; `renewsable --config config/config.example.json run` with a two-profile config invokes Uploader twice with distinct folders
  - _Requirements: 6.1, 6.3, 7.1, 7.2_
  - _Boundary: CLI_
  - _Depends: 2.1, 3.1_

## 5. Documentation

- [ ] 5.1 (P) Add a "Device profiles" section to the README
  - Add a new section under "Configuration reference" that documents the `device_profile` string/object shorthand and the `device_profiles` list, each with a JSON example
  - Include a "Built-in profiles" table listing name, page dimensions, default margin, default font size, and default colour for `rm2` and `paper_pro_move`
  - Add an "Upgrade note" sub-section explaining the one-time filename transition (`renewsable-YYYY-MM-DD.pdf` → `renewsable-YYYY-MM-DD-rm2.pdf`) and the one-time orphan PDF on the reMarkable cloud that the operator can delete
  - Include a short paragraph on the `color: false` override for operators who want strict-mono PDFs (noting that the rm2 device grayscales on display regardless)
  - Observable: `grep -E "device_profile|paper_pro_move|Upgrade note" README.md` returns non-empty; README diff shows a new numbered section
  - _Requirements: 3.3, 4.2_
  - _Boundary: docs_

- [ ] 5.2 (P) Extend `PI_VERIFICATION.md` with a multi-profile smoke step
  - Update the existing filename references to the new `renewsable-<date>-<profile>.pdf` form
  - Add one new numbered step: operator copies `config/config.example.json` to a temp config with both `rm2` and `paper_pro_move` profiles (the latter with a distinct `remarkable_folder` such as `/News-Move`), runs `renewsable test-pipeline`, and verifies both dated+suffixed PDFs appear locally in `~/.local/state/renewsable/out/` and in their respective reMarkable cloud folders
  - Leave an Evidence placeholder under the new step for the operator to paste command output
  - Observable: `grep -E "renewsable-.*-rm2\.pdf|renewsable-.*-paper_pro_move\.pdf|Multi-profile" PI_VERIFICATION.md` returns non-empty
  - _Requirements: 6.1, 6.2, 7.1_
  - _Boundary: docs_

## 6. Example config update

- [ ] 6.1 (P) Annotate `config/README.md` with a multi-profile example
  - Keep the shipped `config/config.example.json` as a single-profile-by-default file (no profile key declared) so operators who copy it see unchanged behaviour
  - In `config/README.md`, add a new section "Device profiles" showing three JSON snippets: the shorthand `"device_profile": "paper_pro_move"`, the object form with a `remarkable_folder` override, and the full `device_profiles` list form for shared deployments
  - Reference the README's built-in profile table instead of duplicating it
  - Observable: `grep -E "device_profile|paper_pro_move|device_profiles" config/README.md` returns non-empty
  - _Requirements: 3.3, 4.2_
  - _Boundary: docs_
