# Pi End-to-End Verification Checklist

This document is the acceptance checklist for task 6.1: **end-to-end smoke test against the real tooling** on a Raspberry Pi. It covers requirements 3.2, 5.1, 5.3, 5.4, 6.5, 8.1, 8.2 of the `daily-paper` spec.

Run these steps **on the Pi**, in order, from a checkout of `renewsable` that has been bootstrapped with `scripts/install-pi.sh`. Each step is self-contained: run the command(s), check the success criteria, and paste the captured evidence under the `Evidence:` placeholder.

Leave blanks filled in on this document (or in a sibling `PI_VERIFICATION_RESULT.md`) so that "the checklist has been passed on real hardware" is provable after the fact.

> Conventions
> - `$` prefix = run on the Pi as the non-root user that owns the checkout.
> - All paths are defaults from `config/config.example.json`; adjust if your config overrides `output_dir` / `log_dir` / `remarkable_folder`.
> - `YYYY-MM-DD` = today's local date. The build uses it for the PDF basename.

---

## 1. Prerequisite check — `install-pi.sh` ran cleanly

**Goal:** confirm the venv is populated and the pinned `rmapi` binary is in place before we ask anything else of the system.

Commands:

```bash
$ cd ~/renewsable
$ ./.venv/bin/renewsable --help
$ ./.venv/bin/rmapi version
$ ls -la ./.venv/bin/rmapi
```

Success criteria:
- `renewsable --help` exits 0 and prints the top-level subcommand list (`pair`, `build`, `upload`, `run`, `test-pipeline`, `install-schedule`, `uninstall-schedule`).
- `rmapi version` prints a version string (e.g. `ddvk 0.0.x (…)`) and exits 0.
- `.venv/bin/rmapi` exists, is executable, and is > 1 MB (pinned binary, not a shim).

On failure: re-run `./scripts/install-pi.sh` (idempotent). If `rmapi` is missing, check the install log for SHA-256 mismatch or network errors during the `ddvk/rmapi` tarball download.

Evidence:

```text
# paste: `renewsable --help` output (first ~15 lines)


# paste: `rmapi version` output


# paste: `ls -la .venv/bin/rmapi`

```

---

## 2. Pairing — reMarkable cloud token present

**Goal:** complete the one-time `rmapi` pairing and confirm the device token persists. (Requirement 3.2 precondition.)

Commands:

```bash
$ cd ~/renewsable
$ source .venv/bin/activate
$ renewsable pair
# In a browser logged into the same reMarkable account, open:
#   https://my.remarkable.com/device/desktop/connect
# Copy the 8-character one-time code and paste it at the prompt.
$ ls -la ~/.config/rmapi/rmapi.conf
$ wc -c ~/.config/rmapi/rmapi.conf
```

Success criteria:
- `renewsable pair` prints a success line (e.g. `pairing complete` / `device registered`) and exits 0.
- `~/.config/rmapi/rmapi.conf` exists and is non-empty (tens to hundreds of bytes; contains a device token).
- Subsequent `rmapi ls /` works without prompting.

On failure:
- "invalid code" → the one-time code expired (they time out in ~60 s). Request a fresh one and retry.
- "device was removed" (later, during upload) → re-pair with `renewsable pair --force`.

Evidence:

```text
# paste: terminal transcript of `renewsable pair` (redact the one-time code)


# paste: `ls -la ~/.config/rmapi/rmapi.conf` and `wc -c …`

```

---

## 3. Build only — dated PDF appears locally

**Goal:** run the build stage in isolation and confirm a dated PDF lands in the configured output directory. Hungarian content from `telex.hu` must render with correct accents (Requirement 3.2).

Commands:

```bash
$ cd ~/renewsable
$ source .venv/bin/activate
$ renewsable --config config/config.example.json build
$ ls -la ~/.local/state/renewsable/out/
$ TODAY=$(date +%Y-%m-%d)
$ PDF=~/.local/state/renewsable/out/renewsable-${TODAY}.pdf
$ stat -c '%n %s bytes' "$PDF"
$ xxd "$PDF" | head -1
```

Success criteria:
- `renewsable … build` exits 0 and prints the absolute path of the PDF it wrote.
- The file at `~/.local/state/renewsable/out/renewsable-YYYY-MM-DD.pdf` exists and has size > 0.
- `xxd … | head -1` begins with `25504446 2d` (i.e. the literal bytes `%PDF-`).
- Visual check: open the PDF on the Pi (`xdg-open "$PDF"`) or `scp` it back to the dev box. Hungarian articles from `telex.hu` render with accented characters intact (ő, ű, á, é, í, ó, ú) — no tofu boxes, no mojibake.

On failure:
- `BuildError: all feeds failed` → network or feed-side outage; re-run. Per-feed failures are logged but tolerated as long as at least one feed yields stories.
- Missing fonts for Hungarian glyphs → re-run `./scripts/install-pi.sh`; it ensures DejaVu and Noto Core are installed.

Evidence:

```text
# paste: `renewsable … build` stdout (full)


# paste: `ls -la ~/.local/state/renewsable/out/`


# paste: `stat` output for today's PDF


# paste: first line of `xxd "$PDF" | head -1`  (must start with `25504446 2d`)


# note: "Hungarian accents rendered correctly: YES / NO"

```

---

## 4. Upload only — PDF appears on the reMarkable cloud

**Goal:** push the just-built PDF to the `/News/` folder on the reMarkable cloud and confirm via `rmapi ls`.

Commands:

```bash
$ cd ~/renewsable
$ source .venv/bin/activate
$ renewsable --config config/config.example.json upload
$ rmapi ls /News
```

Success criteria:
- `renewsable … upload` exits 0. Log contains `upload complete` (or equivalent) with the target path `/News/renewsable-YYYY-MM-DD`.
- `rmapi ls /News` lists an entry `renewsable-YYYY-MM-DD` (name only; `rmapi` does not display the `.pdf` suffix for uploaded PDFs — they appear as documents).
- On the tablet itself (after a cloud sync), the document is visible in the **News** folder.

On failure:
- "paired device was removed" → token was revoked cloud-side. `renewsable pair --force` and retry.
- Timeout / network → `upload_retries` (default 3) already retries internally; a hard failure here means the cloud API is actually unreachable.

Evidence:

```text
# paste: `renewsable … upload` stdout (full)


# paste: `rmapi ls /News` output showing the dated entry

```

---

## 5. Combined run — `renewsable run` builds then uploads

**Goal:** exercise the quiet, production-shaped single-command path that the timer will invoke. (Requirements 4.1, 4.5.)

Commands:

```bash
$ cd ~/renewsable
$ source .venv/bin/activate
$ renewsable --config config/config.example.json run
$ echo "exit=$?"
```

Success criteria:
- Exits 0.
- Both a new local PDF (same name, overwritten in place) and a new/updated `/News/renewsable-YYYY-MM-DD` on the cloud. Re-uploading is fine — the upload uses `rmapi put --force`.
- Log output is concise (INFO-level only, no `DEBUG`), consistent with what will be written when the timer fires it.

Evidence:

```text
# paste: full stdout + stderr of `renewsable … run`


# paste: `echo "exit=$?"` -> "exit=0"

```

---

## 6. End-to-end `test-pipeline` — verbose, dev-facing run

**Goal:** exercise the verbose smoke-test entry point. (Requirement 6.5.)

Commands:

```bash
$ cd ~/renewsable
$ source .venv/bin/activate
$ renewsable --config config/config.example.json test-pipeline
$ echo "exit=$?"
$ rmapi ls /News
```

Success criteria:
- Exits 0. Output is verbose (at least INFO-level; build phase, upload phase, and per-feed progress visible).
- Local PDF at `~/.local/state/renewsable/out/renewsable-YYYY-MM-DD.pdf` is updated (mtime within the last minute).
- `rmapi ls /News` still shows `renewsable-YYYY-MM-DD` (timestamp updated on the cloud side).

Evidence:

```text
# paste: full `renewsable … test-pipeline` output (trim feed bodies if long; keep phase markers)


# paste: `rmapi ls /News`


# paste: `ls -la --time=modification ~/.local/state/renewsable/out/renewsable-*.pdf`

```

---

## 7. Schedule install + fire test — timer actually triggers

**Goal:** prove the `systemd --user` timer fires on schedule and that both journald and the plain-text log file capture the run. (Requirements 5.1, 5.3, 5.4, 8.1, 8.2.)

### 7a. Install a temporary schedule 2 minutes in the future

```bash
$ cd ~/renewsable
$ source .venv/bin/activate
$ cp config/config.example.json /tmp/test-config.json
$ NEW_TIME=$(date -d "+2 minutes" +%H:%M)   # e.g. 14:37
$ echo "scheduling for $NEW_TIME"
$ # Edit schedule_time in /tmp/test-config.json to $NEW_TIME.
$ # Quick sed (BSD/GNU both handle this form):
$ python3 - <<PY
import json, pathlib
p = pathlib.Path("/tmp/test-config.json")
c = json.loads(p.read_text())
import os
c["schedule_time"] = os.environ["NEW_TIME"]
p.write_text(json.dumps(c, indent=2) + "\n")
print("schedule_time =", c["schedule_time"])
PY
$ renewsable --config /tmp/test-config.json install-schedule
$ systemctl --user list-timers | grep renewsable
```

Success criteria for 7a:
- `install-schedule` exits 0 and prints that it wrote `renewsable.service` and `renewsable.timer` under `~/.config/systemd/user/`.
- `systemctl --user list-timers | grep renewsable` shows a `NEXT` field roughly 2 minutes from now.

Evidence (7a):

```text
# paste: `install-schedule` stdout


# paste: `systemctl --user list-timers | grep renewsable`  (NEXT column visible)


# paste: value of $NEW_TIME so the reviewer can reconcile against journalctl timestamps

```

### 7b. Wait for the fire, then verify journald + plain-text log

Wait until at least 3 minutes past `NEW_TIME` so the run has time to complete.

```bash
$ sleep 180
$ journalctl --user -u renewsable.service --since "5 minutes ago" --no-pager
$ systemctl --user show renewsable.service -p ExecMainStatus -p Result
$ tail -n 100 ~/.local/state/renewsable/logs/renewsable.log
```

Success criteria for 7b:
- `journalctl` output shows a run that started at `NEW_TIME` (±60 s), progressed through build + upload, and ended with `Main process exited, code=exited, status=0/SUCCESS` (or equivalent) — i.e. service exit code 0. (Req 5.1, 5.3, 5.4.)
- `ExecMainStatus=0` and `Result=success`.
- `~/.local/state/renewsable/logs/renewsable.log` contains the same run — dated records with the matching timestamp, covering build and upload, with no stray credentials or one-time codes in the text. (Req 8.1, 8.2.)
- A matching PDF appears on the reMarkable cloud under `/News/`: `rmapi ls /News`.

On failure:
- Timer did not fire → check `loginctl show-user $USER -p Linger` (see step 8). If `Linger=no`, the user session is gone and the timer is inert.
- Timer fired but service failed → read `journalctl … --no-pager` for the traceback; the plain-text log will usually carry the same traceback minus credentials.

Evidence (7b):

```text
# paste: `journalctl --user -u renewsable.service --since "5 minutes ago"` (full, redact tokens if any slip through)


# paste: `systemctl --user show renewsable.service -p ExecMainStatus -p Result`


# paste: last ~30 lines of `~/.local/state/renewsable/logs/renewsable.log`


# paste: `rmapi ls /News` showing the dated entry has updated mtime

```

### 7c. Restore the real schedule

```bash
$ cd ~/renewsable
$ source .venv/bin/activate
$ renewsable --config config/config.example.json install-schedule
$ systemctl --user list-timers | grep renewsable
$ rm /tmp/test-config.json
```

Success criteria for 7c:
- `install-schedule` re-runs idempotently, overwriting the unit files.
- `list-timers` now shows the `NEXT` field at `05:30` local on the next calendar day.

Evidence (7c):

```text
# paste: `install-schedule` stdout


# paste: `systemctl --user list-timers | grep renewsable`  (NEXT should be 05:30 next day)

```

---

## 8. Linger — timer survives logout

**Goal:** confirm `loginctl enable-linger $USER` has been run, so the user-level `systemd` instance (and therefore the timer) stays alive when no SSH session is open.

Commands:

```bash
$ loginctl show-user $USER -p Linger
```

Success criteria:
- Output is exactly `Linger=yes`.

If `Linger=no`:

```bash
$ sudo loginctl enable-linger $USER
$ loginctl show-user $USER -p Linger
```

Re-check; should now say `Linger=yes`. Re-verify step 7 after enabling if it had failed.

Evidence:

```text
# paste: `loginctl show-user $USER -p Linger`  (must be `Linger=yes`)

```

---

## Requirement coverage

| Requirement | Covered by step(s) |
|-------------|--------------------|
| 3.2 — PDF with correct non-ASCII (Hungarian) rendering | 3 |
| 5.1 — systemd user timer installed and enabled | 7a, 8 |
| 5.3 — timer fires at the configured wall-clock time | 7b |
| 5.4 — service run succeeds end-to-end under the timer | 7b |
| 6.5 — `test-pipeline` end-to-end entry point works | 6 |
| 8.1 — plain-text log file captures runs | 7b |
| 8.2 — log file and journald agree on the same run | 7b |

## Sign-off

After every step above has an `Evidence:` block filled in and matches the Success criteria, record:

- Date of verification run: `____________________`
- Operator (you): `____________________`
- Pi hostname / OS release (`uname -a` + `/etc/os-release PRETTY_NAME`): `____________________`
- Overall result: PASS / FAIL (circle one)
- Notes / deviations: `____________________`

A PASS here satisfies task 6.1's observable.
