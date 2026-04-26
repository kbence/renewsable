# renewsable example configuration

This directory ships [`config.example.json`](./config.example.json), a ready-to-run
configuration that wakes up every morning at 05:30, fetches six international
feeds (with telex.hu as the only non-English source), and uploads the resulting
paper to the `/News` folder on the reMarkable.

> **Why this README exists.** JSON does not permit comments. Rather than
> maintain a hand-commented YAML mirror or teach users to strip `//` before
> loading, the schema documentation lives here, next to the file it
> describes. Copy `config.example.json`, edit it, and treat this README as
> the authoritative field reference.

## Copy into place

`Config.load(path)` reads exactly the path you give it; `--config` on the CLI
is the canonical way to point renewsable at a specific file. A common home is
the XDG config directory:

```bash
mkdir -p ~/.config/renewsable
cp config/config.example.json ~/.config/renewsable/config.json
renewsable build --config ~/.config/renewsable/config.json
```

`renewsable` validates the file before doing anything irreversible; a missing,
malformed, or schema-violating config raises `ConfigError` with the field name
and the exact path of the file that needs editing (Requirements 1.3 / 1.4).

## Top-level schema

Required fields **must** be present; the loader rejects unknown top-level keys
so typos like `stoires` or `schedule_tlme` fail fast instead of silently
falling back to a default.

| Field                  | Type             | Required | Default                                  | Meaning                                                                 |
|------------------------|------------------|----------|------------------------------------------|-------------------------------------------------------------------------|
| `schedule_time`        | string `HH:MM`   | yes      | —                                        | 24-hour local wall-clock time when the daily build runs.                 |
| `remarkable_folder`    | string           | yes      | —                                        | Destination folder on the reMarkable. Must start with `/` (e.g. `/News`). |
| `stories`              | list of objects  | yes      | —                                        | Non-empty list of goosepaper story providers. See below.                 |
| `output_dir`           | string (path)    | no       | XDG state dir (`$XDG_STATE_HOME/renewsable` or `~/.local/state/renewsable`) | Where generated PDFs / EPUBs are written. Supports `~` expansion.         |
| `font_size`            | integer          | no       | goosepaper default                       | Forwarded to goosepaper when present.                                    |
| `log_dir`              | string (path)    | no       | XDG state dir log subfolder              | Where rotating log files land. Supports `~` expansion.                   |
| `user_agent`           | string           | no       | `renewsable/0.1 (+https://github.com/kbence/renewsable)` | User-Agent sent on feed fetches.                           |
| `goosepaper_bin`       | string           | no       | `goosepaper`                             | Command name or absolute path to the goosepaper executable.             |
| `rmapi_bin`            | string           | no       | `rmapi`                                  | Command name or absolute path to the rmapi executable.                   |
| `feed_fetch_retries`   | integer > 0      | no       | `3`                                      | How many times a failing feed fetch is retried before giving up.         |
| `feed_fetch_backoff_s` | number > 0       | no       | `1.0`                                    | Base seconds between feed-fetch retries (exponential, capped internally).|
| `upload_retries`       | integer > 0      | no       | `3`                                      | How many times a failing reMarkable upload is retried.                   |
| `upload_backoff_s`     | number > 0       | no       | `2.0`                                    | Base seconds between upload retries.                                     |
| `subprocess_timeout_s` | integer > 0      | no       | `180`                                    | Hard timeout for `goosepaper` and `rmapi` subprocess invocations.        |

`output_dir` is intentionally **absent** from the example so the default XDG
location is exercised; uncomment (i.e. add) the key only if you need a custom
path.

## `stories` — goosepaper provider schema

Each entry is a `{provider, config}` object handed straight to
[goosepaper](https://github.com/j6k4m8/goosepaper). `renewsable` does not
validate the `config` payload beyond "it is a dict"; goosepaper owns the
per-provider contract. The example uses goosepaper's built-in `rss` provider,
which accepts:

| Field      | Type    | Meaning                                                                |
|------------|---------|------------------------------------------------------------------------|
| `rss_path` | string  | Feed URL (Atom or RSS). Required.                                      |
| `limit`    | integer | Maximum number of items to pull from the feed. Optional; we default to 5. |

Other providers documented by goosepaper (`wikipedia_current_events`,
`reddit`, `hackernews`, etc.) also work — just use the same `{provider,
config}` shape. See goosepaper's `stories` module for the full list and
per-provider fields: <https://github.com/j6k4m8/goosepaper>.

### Feeds shipped in the example

| Source                 | URL                                                                 |
|------------------------|---------------------------------------------------------------------|
| telex.hu               | <https://telex.hu/rss>                                              |
| BBC World              | <https://feeds.bbci.co.uk/news/world/rss.xml>                       |
| NYT Homepage           | <https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml>         |
| Guardian International | <https://www.theguardian.com/international/rss>                     |
| The Economist (weekly) | <https://www.economist.com/the-world-this-week/rss.xml>             |
| Hacker News front page | <https://hnrss.org/frontpage>                                       |

Add or remove entries by editing the `stories` array. The loader requires the
array to be **non-empty**; an empty list raises `ConfigError` with a remediation
hint.

## Device profiles

The shipped example is a **single-profile** config — it omits the profile key and gets the built-in `rm2` default so operators upgrading from the pre-profile version see no behavioural change. To tune the PDF for a different device, add one of these three shapes:

**Shorthand (single profile, built-in defaults)**:
```json
{ "device_profile": "paper_pro_move" }
```

**Single profile with overrides (e.g. distinct destination folder)**:
```json
{
  "device_profile": {
    "name": "paper_pro_move",
    "remarkable_folder": "/News-Move"
  }
}
```

**Multi-profile (one PDF per profile per run, typical for a shared deployment)**:
```json
{
  "device_profiles": [
    { "name": "rm2" },
    { "name": "paper_pro_move", "remarkable_folder": "/News-Move" }
  ]
}
```

Supported built-in profiles, override keys, and the strict-mono `color: false` toggle are documented in the project README's "Device profiles" section. A config may declare either `device_profile` or `device_profiles`, not both.

## Validation sanity check

Before deploying a modified copy, run:

```bash
renewsable build --config /path/to/your/config.json --dry-run
```

A dry-run exits with code 0 only when the file parses cleanly and all
invariants hold; any `ConfigError` message names the offending field and the
exact file path, per Requirement 1.4.
