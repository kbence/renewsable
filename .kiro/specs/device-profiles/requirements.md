# Requirements Document

## Project Description (Input)

**Who has the problem.** Two operator personas:
- **Primary ("the rM 2 user")** — runs renewsable today against a reMarkable 2 (10.3", monochrome). No change in their experience is desired as a side-effect of this spec.
- **New ("the Paper Pro Move user")** — a renewsable operator who reads on a reMarkable Paper Pro Move (7.3", color e-ink, released late 2025). Today they would receive a document sized for the larger rM 2 screen with no color-aware rendering.

**Current situation.** The `daily-paper` spec's Builder produces a single PDF whose page size and typography are implicitly tuned for the reMarkable 2. There is no device-profile concept in configuration, no color-aware rendering, and the upload destination folder is a single string per config file.

**What should change.** Introduce a **device profile** concept driven by configuration. The operator selects a profile (at minimum `rm2` and `paper_pro_move`) and the Builder produces a PDF whose page size is tuned to that device. The default remains `rm2` so the rM 2 user's experience is unchanged unless they opt in. Color support is desirable on the Paper Pro Move profile **only if it's cheap to add** (for example, if goosepaper / the underlying style CSS can emit colored headlines or section tints behind a simple toggle); otherwise the Paper Pro Move profile renders mono and a follow-on spec can add color later. Per-feed `limit` values stay as the operator set them — the smaller screen does not automatically reduce content volume. If feasible within the same spec, allow a single run to produce **one PDF per configured profile** and upload each to a (possibly per-profile) reMarkable folder, so one deployment can serve both personas; otherwise this spec ships single-profile-per-run and multi-device delivery becomes a follow-on.

Out of scope for this spec: scheduling changes, new upload mechanisms, broadsheet-layout overhauls, typography overhauls beyond page-size selection, new reMarkable devices beyond rm2 and Paper Pro Move.

## Requirements
<!-- Will be generated in /kiro-spec-requirements phase -->
