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
falling back to a default. Legacy keys that used to control goosepaper or the
device-profile system (`font_size`, `goosepaper_bin`, `subprocess_timeout_s`,
`device_profile`, `device_profiles`) are explicitly rejected with a remediation
message; remove them when migrating an old config.

| Field                  | Type             | Required | Default                                  | Meaning                                                                 |
|------------------------|------------------|----------|------------------------------------------|-------------------------------------------------------------------------|
| `schedule_time`        | string `HH:MM`   | yes      | —                                        | 24-hour local wall-clock time when the daily build runs.                 |
| `remarkable_folder`    | string           | yes      | —                                        | Destination folder on the reMarkable. Must start with `/` (e.g. `/News`). |
| `stories`              | list of objects  | yes      | —                                        | Non-empty list of RSS feed sources. See below for the closed-set schema. |
| `output_dir`           | string (path)    | no       | XDG state dir (`$XDG_STATE_HOME/renewsable` or `~/.local/state/renewsable`) | Where generated EPUBs are written. Supports `~` expansion.               |
| `log_dir`              | string (path)    | no       | XDG state dir log subfolder              | Where rotating log files land. Supports `~` expansion.                   |
| `user_agent`           | string           | no       | `renewsable/0.1 (+https://github.com/kbence/renewsable)` | User-Agent sent on feed and article fetches.               |
| `rmapi_bin`            | string           | no       | `rmapi`                                  | Command name or absolute path to the rmapi executable.                   |
| `feed_fetch_retries`   | integer > 0      | no       | `3`                                      | How many times a failing feed/article fetch is retried before giving up. |
| `feed_fetch_backoff_s` | number > 0       | no       | `1.0`                                    | Base seconds between feed-fetch retries (exponential, capped internally).|
| `upload_retries`       | integer > 0      | no       | `3`                                      | How many times a failing reMarkable upload is retried.                   |
| `upload_backoff_s`     | number > 0       | no       | `2.0`                                    | Base seconds between upload retries.                                     |

`output_dir` is intentionally **absent** from the example so the default XDG
location is exercised; uncomment (i.e. add) the key only if you need a custom
path.

## `stories` — RSS feed source schema

Each entry is a closed-set `{provider, config}` object validated by
`Config.load`. Only the RSS provider is supported; renewsable parses feeds
in-process via [`feedparser`](https://feedparser.readthedocs.io/) and extracts
each article's main content via
[`trafilatura`](https://trafilatura.readthedocs.io/) (with `readability-lxml`
as a secondary fallback).

```json
{
  "provider": "rss",
  "config": {
    "rss_path": "https://example.com/feed.xml",
    "limit": 5
  }
}
```

| Key                | Type    | Required | Meaning                                                                |
|--------------------|---------|----------|------------------------------------------------------------------------|
| `provider`         | string  | yes      | Must equal `"rss"`. Any other value is a `ConfigError`.                |
| `config.rss_path`  | string  | yes      | Feed URL. Must start with `http://` or `https://`.                     |
| `config.limit`     | int > 0 | no       | Maximum number of items pulled from this source per build. Omitted means no per-source cap. |

Unknown keys at either level (e.g., `style`, `font_size`, or any goosepaper
provider beyond `rss`) are rejected with a `ConfigError` naming the offending
field. This is the migration error path for configs left over from the
goosepaper era.

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

## Validation sanity check

Before deploying a modified copy, attempt a build:

```bash
renewsable build --config /path/to/your/config.json
```

The loader validates the file before any real work; any `ConfigError` names
the offending field and the exact file path (Requirement 1.4). You can
interrupt with Ctrl+C once the build moves past the config-load phase if you
only wanted to validate.
