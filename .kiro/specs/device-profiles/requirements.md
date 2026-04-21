# Requirements Document

## Introduction

**Who has the problem.** Two operator personas:
- **Primary ("the rM 2 user")** — runs renewsable today against a reMarkable 2 (10.3", monochrome). No change in their experience is desired as a side-effect of this spec.
- **New ("the Paper Pro Move user")** — a renewsable operator who reads on a reMarkable Paper Pro Move (7.3", color e-ink, released late 2025). Today they would receive a document sized for the larger rM 2 screen with no color-aware rendering.

**Current situation.** The `daily-paper` spec's Builder produces a single PDF whose page size and typography are implicitly tuned for the reMarkable 2. There is no device-profile concept in configuration, no color-aware rendering, and the upload destination folder is a single string per config file.

**What should change.** Introduce a **device profile** concept driven by configuration. The operator selects a profile (at minimum `rm2` and `paper_pro_move`) and the Builder produces a PDF whose page size is tuned to that device. The default remains `rm2` so the rM 2 user's experience is unchanged unless they opt in. Color rendering for the Paper Pro Move profile and multi-profile runs in a single invocation are in scope **only if design confirms they are low-cost additions**; otherwise the Paper Pro Move profile renders mono and a single run produces a single profile's PDF, with the deferred pieces moved to follow-on specs. Per-feed `limit` values stay as the operator set them — the smaller screen does not automatically reduce content volume.

## Boundary Context

- **In scope**:
  - A configurable `device_profile` (or equivalent) field with at minimum the values `rm2` and `paper_pro_move`.
  - Profile-tuned page size for the generated PDF.
  - Preserving existing behavior for configurations that omit the new field (backward compatibility).
  - Clear error reporting on unknown/invalid profile values.
- **Conditionally in scope (gated on design finding a low-cost implementation)**:
  - Color rendering for the Paper Pro Move profile while keeping `rm2` mono. If implementing color requires more than a per-profile style toggle, this is deferred to a follow-on spec and the Paper Pro Move profile ships mono for now.
  - Producing one PDF per profile in a single run, with an optional per-profile reMarkable destination folder. If this requires substantial changes to the build/upload pipeline, it is deferred to a follow-on spec and this spec ships single-profile-per-run.
- **Out of scope**:
  - New reMarkable devices beyond `rm2` and `paper_pro_move`.
  - Custom broadsheet/typography redesign beyond page sizing.
  - Schedule changes, new upload mechanisms, new CLI commands, or rendering-engine swaps.
  - Automatic content-volume reduction (fewer stories on smaller screens); per-feed `limit` values are the operator's.
  - Color calibration tuning for the Paper Pro Move's Gallery 3 palette.
- **Adjacent expectations**:
  - The `daily-paper` spec owns the Builder, config loader, and Uploader; this spec extends their behavior at the existing seams (config schema, page size in the build step, destination folder in the upload step) without introducing new components or external dependencies.
  - The reMarkable cloud accepts the same upload protocol regardless of target device; no pairing, auth, or folder-layout changes beyond potentially adding a per-profile folder key.
  - Any deferred pieces (color, multi-profile runs) remain candidates for later specs that build on this one.

## Requirements

### Requirement 1: Configurable device profile
**Objective:** As the operator, I want to declare which reMarkable device my PDF is tuned for, so that the output is sized for the screen I actually read on.

#### Acceptance Criteria
1. The renewsable system shall allow the operator to declare a device profile in the configuration file.
2. The renewsable system shall accept at minimum the documented profile values `rm2` and `paper_pro_move`.
3. Where the configuration does not declare a device profile, the renewsable system shall apply the `rm2` profile so that pre-existing configurations continue to behave as before.
4. If an unknown profile value is provided, the renewsable system shall fail the current command with an error message that names the invalid value and lists the supported profile values, and shall not produce or upload any PDF.

### Requirement 2: Profile-tuned PDF page size
**Objective:** As the operator, I want each profile to produce a PDF sized for its target device, so that the document opens at natural reading size without pinch-zoom.

#### Acceptance Criteria
1. When the active device profile is `rm2`, the renewsable system shall produce a PDF whose page dimensions are tuned to a 10.3" portrait reMarkable screen.
2. When the active device profile is `paper_pro_move`, the renewsable system shall produce a PDF whose page dimensions are tuned to a 7.3" portrait reMarkable screen.
3. The renewsable system shall keep the page orientation in portrait regardless of profile.
4. The renewsable system shall apply the profile's page dimensions to every page of the document for the active run.

### Requirement 3: Built-in profiles ship out of the box
**Objective:** As a new Paper Pro Move operator, I want built-in profiles that work without defining custom page-size tables, so that I can start using the tool immediately.

#### Acceptance Criteria
1. The renewsable system shall ship built-in, named profiles `rm2` and `paper_pro_move` with sensible page-size defaults for each device.
2. Where the operator selects a built-in profile by name, the renewsable system shall apply that profile's defaults without requiring any additional configuration fields.
3. The renewsable system shall document, in the operator-facing configuration reference, the page dimensions associated with each built-in profile.

### Requirement 4: Backward compatibility for existing rm2 deployments
**Objective:** As an existing rM 2 operator, I want my current configuration file to keep working unchanged, so that upgrading renewsable does not break my morning paper.

#### Acceptance Criteria
1. When a configuration file written for the previous version of renewsable (with no profile-related fields) is loaded, the renewsable system shall produce a PDF whose page size, color behavior, and destination folder match the pre-upgrade output.
2. The renewsable system shall not require the operator to edit their configuration file as a condition of upgrading to the version that introduces device profiles.
3. The renewsable system shall not emit a warning-level or higher log entry solely because a configuration omits profile-related fields.

### Requirement 5: Color rendering on the Paper Pro Move profile *(conditional on design)*
**Objective:** As a Paper Pro Move operator, I want colored content in the feeds to render in color on my tablet, so that I benefit from my color screen.

#### Acceptance Criteria
1. Where the active device profile is `paper_pro_move` and color rendering is enabled, the renewsable system shall preserve color in feed-provided images and in any profile-specific styling that uses color.
2. Where the active device profile is `rm2`, the renewsable system shall render the PDF in monochrome (or grayscale) regardless of feed content.
3. The renewsable system shall allow the operator to override the profile's color behavior via a configuration flag (for example, to force the Paper Pro Move profile to render in mono).

_Note: the entire requirement is conditional per the Boundary Context. If design concludes it cannot be implemented at low cost, these criteria move to a follow-on spec and the `paper_pro_move` profile ships mono._

### Requirement 6: Multi-profile output in a single run *(conditional on design)*
**Objective:** As a shared-deployment operator, I want one scheduled run to produce one PDF per configured profile so that multiple readers on different devices each receive an appropriately-sized file.

#### Acceptance Criteria
1. Where the configuration declares more than one device profile to produce, the renewsable system shall produce one PDF per declared profile during a single build invocation.
2. The renewsable system shall name each multi-profile output PDF such that the profile it was built for is identifiable from the filename.
3. If one profile's build fails while another succeeds, the renewsable system shall continue with the remaining profiles, log the failed profile with its reason, and exit with a non-zero status at the end if any profile failed.

_Note: the entire requirement is conditional per the Boundary Context. If design concludes it cannot be implemented at low cost, these criteria move to a follow-on spec and this spec ships single-profile-per-run._

### Requirement 7: Per-profile reMarkable destination folder *(conditional on Requirement 6)*
**Objective:** As a shared-deployment operator, I want each profile's PDF to land in a device-specific folder on the reMarkable cloud, so that the right document appears on the right tablet.

#### Acceptance Criteria
1. Where the configuration declares a per-profile destination folder, the renewsable system shall upload each profile's PDF to its declared folder.
2. Where no per-profile destination folder is declared, the renewsable system shall upload every profile's PDF to the single default destination folder, relying on the filename (Requirement 6.2) to distinguish them.
3. Where Requirement 6 is deferred, Requirement 7 shall also be deferred to the same follow-on spec; the single-profile configuration retains the existing single destination folder semantics.

### Requirement 8: Content volume preservation across profiles
**Objective:** As the operator, I want switching profiles to change only the PDF's layout, not how much content it contains, so that my edited `limit` values remain the source of truth.

#### Acceptance Criteria
1. The renewsable system shall not modify any feed's configured `limit` value based on the active device profile.
2. When the same configuration file is built under two different profiles in sequence, the renewsable system shall include the same number of stories per feed in each resulting PDF.

### Requirement 9: Error reporting for profile-related configuration issues
**Objective:** As the operator, I want clear errors for typos or invalid profile-related configuration, so that I can fix mistakes quickly.

#### Acceptance Criteria
1. When the configuration includes per-profile settings (such as a color override or destination folder) keyed on a profile name that is not one of the supported profiles, the renewsable system shall fail with an error naming the unknown profile and the supported alternatives.
2. When the configuration declares the same device profile more than once in a multi-profile list, the renewsable system shall fail with an error naming the duplicated profile.
3. When a profile-related configuration field has a malformed value (for example, a non-string profile name, a non-boolean color toggle), the renewsable system shall fail with an error naming the field and the expected value type.
4. The renewsable system shall surface all such configuration errors before starting any feed fetch, PDF build, or upload work.
