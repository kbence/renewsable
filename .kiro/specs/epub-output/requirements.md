# Requirements Document

## Introduction

Renewsable currently produces a daily PDF using goosepaper (WeasyPrint under the hood) and uploads it to reMarkable. The PDF reflows poorly on e-readers, lacks a usable navigation table of contents, and has missing or broken images. This spec replaces the PDF output with a directly built EPUB, removes the goosepaper dependency entirely, adds an EPUB navigation TOC, and ensures article images are fetched, embedded, and rendered with a visible fallback when fetch fails. As a side effect of switching to a reflowable format, multi-profile output collapses to a single EPUB per run.

## Boundary Context

- **In scope**:
  - EPUB as the sole output format produced by `Builder.build()`.
  - Removal of the goosepaper dependency, its subprocess invocation, its config-serialization path, and any PDF-specific validation/tests.
  - Article fetch and main-content extraction (the work goosepaper used to do for full-text articles).
  - Image fetch and embedding into the EPUB, with a visible alt-text placeholder when an image cannot be fetched.
  - EPUB navigation TOC (EPUB3 nav document) and standard EPUB metadata (title, author, date).
  - Upload of the produced EPUB to reMarkable.
- **Out of scope**:
  - Producing PDF output, in any form, behind any flag, in this or future runs.
  - An inline printed TOC page within the book body.
  - A generated cover image.
  - Profile-tuned page dimensions, margins, and font-size CSS — these have no meaning for a reflowable EPUB.
  - Multi-profile output: producing more than one file per run.
- **Adjacent expectations**:
  - This spec supersedes the device-profiles spec's user-observable behavior of producing one output file per device profile (Req 6 of `device-profiles`) and the profile suffix in the filename (Req 6.2). Renewsable will produce exactly one EPUB per run, with no profile suffix in the filename. Any internal device-profile configuration that no longer affects user-observable output is left to design to clean up.
  - The reMarkable target folder is expected to remain a single configured value (the per-profile folder override from device-profiles Req 7 no longer has a single-output to attach to).
  - Article fetch is expected to honor the existing robots.txt and retry/backoff behavior already used for RSS feed fetch.

## Requirements

### Requirement 1: EPUB as the sole output format

**Objective:** As the operator, I want renewsable to produce a valid EPUB file each day, so that the daily paper renders well on my reMarkable and other e-readers.

#### Acceptance Criteria

1. When the operator runs the build, renewsable shall produce exactly one EPUB file per run as the build artefact.
2. The renewsable build shall produce a file that is a valid EPUB 3 document, openable by standard EPUB readers without format errors.
3. The renewsable build shall not produce a PDF file under any circumstance.
4. If the produced file is missing, empty, or fails EPUB validity checks, then renewsable shall raise a build error and shall not leave a partial artefact in the output directory.

### Requirement 2: Removal of goosepaper and PDF code paths

**Objective:** As the operator, I want goosepaper and all PDF-specific code removed, so that the project has one well-understood output path.

#### Acceptance Criteria

1. The renewsable runtime shall not invoke goosepaper, WeasyPrint, or any PDF-rendering subprocess during a build.
2. The renewsable project shall not declare goosepaper as a runtime or test dependency.
3. The renewsable test suite shall not contain tests that assert PDF output, PDF magic bytes, or goosepaper invocation behavior.
4. When the operator inspects the configuration schema, renewsable shall not accept goosepaper-specific configuration keys (for example, `style`, `font_size` as goosepaper-style overrides) as required input for producing the daily paper.

### Requirement 3: Article fetch and content extraction

**Objective:** As a reader, I want each story to contain the full article text rather than just the RSS summary, so that the daily paper is readable end-to-end without leaving the device.

#### Acceptance Criteria

1. For each RSS entry selected for the daily paper, renewsable shall fetch the linked article URL and extract its main readable content.
2. While fetching article URLs, renewsable shall apply the same robots.txt check and retry/backoff policy already used for RSS feed fetches.
3. If article extraction fails for an individual story after retries, then renewsable shall fall back to the RSS entry's own description/content field for that story and shall continue building the paper.
4. If both article extraction and the RSS entry's description/content are unusable for a story, then renewsable shall omit that story and shall continue building the paper.
5. If every story in the configured feeds is omitted under criterion 4, then renewsable shall raise a build error and shall not produce an EPUB.

### Requirement 4: Image embedding with visible fallback

**Objective:** As a reader, I want article images to appear inside the EPUB, so that the paper is not full of broken-image gaps when read offline.

#### Acceptance Criteria

1. For each image referenced by an article included in the EPUB, renewsable shall fetch the image and embed it as an internal resource of the EPUB so that the EPUB renders correctly without network access.
2. The renewsable build shall reference embedded images via the EPUB's internal paths, not via remote URLs, in the rendered article content.
3. If an image cannot be fetched after retries, then renewsable shall replace the image in the rendered article with a visible alt-text placeholder that identifies the image as unavailable, and shall continue building the EPUB.
4. The renewsable build shall not fail the run because of individual image fetch failures.

### Requirement 5: EPUB navigation TOC

**Objective:** As a reader, I want the e-reader's built-in navigation menu to list every article in the paper, so that I can jump directly to any story.

#### Acceptance Criteria

1. The renewsable build shall produce an EPUB navigation document (EPUB3 nav) that contains one entry per included article, in the order articles appear in the book.
2. When the reader opens the EPUB and uses the e-reader's navigation menu, the EPUB shall expose each navigation entry as a working link to the corresponding article's first content position.
3. The renewsable build shall use each article's title as the navigation entry label.

### Requirement 6: EPUB metadata

**Objective:** As the operator, I want the daily EPUB to carry sensible identifying metadata, so that the file is easy to recognize on the device and in the cloud.

#### Acceptance Criteria

1. The renewsable build shall set the EPUB's title metadata to `Renewsable Daily — <YYYY-MM-DD>`, where the date is the build's issue date.
2. The renewsable build shall set the EPUB's author metadata to `Renewsable`.
3. The renewsable build shall set the EPUB's publication-date metadata to the build's issue date.
4. The renewsable build shall set a stable language metadata value for the EPUB.

### Requirement 7: Single-output collapse and naming

**Objective:** As the operator, I want exactly one EPUB per run with a predictable name, so that the upload step and re-runs behave deterministically.

#### Acceptance Criteria

1. The renewsable build shall produce exactly one output file per run, regardless of any device-profile configuration present.
2. The renewsable build shall name the output file `renewsable-<YYYY-MM-DD>.epub` using the build's issue date and shall not include a device-profile suffix in the filename.
3. When the operator re-runs the build for the same date, renewsable shall overwrite the existing EPUB for that date in the output directory.

### Requirement 8: Upload of the EPUB to reMarkable

**Objective:** As the operator, I want the daily EPUB uploaded to my reMarkable in the same way the daily PDF was, so that the device-side workflow is unchanged.

#### Acceptance Criteria

1. After a successful build, the renewsable run flow shall upload the produced EPUB to the configured reMarkable folder.
2. When an EPUB for the same date already exists in the configured reMarkable folder, the renewsable upload shall replace it.
3. If the upload fails for transient reasons, then renewsable shall retry according to the existing upload retry policy.
4. If the upload fails for non-retryable reasons (for example, authentication failure), then renewsable shall surface the error and shall not silently succeed.
