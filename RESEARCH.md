# renewsable — Research

Research notes for a program that, each morning, pulls news from the web, composes a newspaper-like document, and uploads it to the user's reMarkable tablet.

Target environment: macOS (darwin), solo developer, reMarkable 2 or reMarkable Paper Pro.

---

## 1. Scope and constraints

- **Device target.** reMarkable 2 (10.3", 1872x1404, monochrome) and reMarkable Paper Pro (11.8", 2160x1620, 4:3, color e-ink). Both natively open **PDF and EPUB**. Portrait orientation is the default. ([reMarkable Paper Pro specs](https://remarkable.com/products/remarkable-paper/pro/details/features), [eWritable Paper Pro review](https://ewritable.net/brands/remarkable/tablets/remarkable-paper-pro/))
- **Cadence.** Once per day, early morning, before the user wakes. The job must be resilient to the Mac being asleep or briefly offline.
- **Deliverable.** A single document per day, named e.g. `renewsable-2026-04-18.pdf`, placed in a dedicated reMarkable folder (e.g. `/News/`).
- **Operator.** Solo developer — low maintenance burden is a first-class concern.

---

## 2. News collection

### 2.1 Source types at a glance

| Source type | Pros | Cons |
|---|---|---|
| RSS/Atom feeds | Free, universal, low/no rate limits, tiny payloads, publisher-sanctioned | Titles + summary only on many feeds; full article requires a second fetch |
| News aggregator APIs (NewsAPI, GNews, etc.) | Unified schema, search across outlets | Free tiers are small, often non-commercial-only, some localhost-only, can lag publisher feeds |
| Publisher APIs (NYT, Guardian) | Deep archives, full text from that publisher | One per outlet, rate-limited, NYT does not give body text in free tier |
| Web scraping | Works on anything | Fragile, ethically gray, paywall-hostile |

For a **personal** daily digest, RSS + full-article extraction is the sweet spot. Aggregator APIs only become compelling if you want search/filtering across the entire news landscape.

### 2.2 News aggregator APIs — free-tier reality check (2026)

| API | Free tier | Commercial on free? | Notes |
|---|---|---|---|
| [NewsAPI.org](https://newsapi.org) | 100 req/day, localhost only | No | Paid plans start at ~$449/mo; dev-only in practice |
| [GNews](https://gnews.io) | 100 req/day, ~1 req/sec | No (dev/testing only) | Broader free than NewsAPI for reading |
| [Guardian Open Platform](https://open-platform.theguardian.com/) | Free key, generous, **includes full body text** | Yes (non-commercial) | Single-publisher but high quality |
| [NYT Article Search API](https://developer.nytimes.com/) | Free, 4000 req/day, 10 req/min | No body text | Metadata + snippet only |
| [Associated Press](https://developer.ap.org) | Enterprise only | n/a | Not practical for solo dev |
| [Mediastack](https://mediastack.com) | 500 req/month | Limited | Stingy free tier |
| [Currents API](https://currentsapi.services) | 600 req/day | Limited | Reasonable for a daily job |
| [NewsData.io](https://newsdata.io) | 200 req/day | No | Similar to GNews |
| Bing News Search | Retired / folded into Azure AI | — | Effectively gone as a cheap option |

Sources: [Free News APIs 2026 comparison](https://publicapis.io/blog/free-news-apis), [NewsAPI alternatives 2026](https://newsmesh.co/blog/newsapi-alternatives-2026), [newsdata.io comparison](https://newsdata.io/blog/news-api-comparison/).

**Takeaway:** for a once-a-day personal tool, an API key will be used ~1 time per day, so free tiers are fine, but the APIs don't really buy you much over RSS. Use the Guardian API if you want one reliable source with full body text without scraping; otherwise RSS-first.

### 2.3 RSS parsing

- **Python:** [`feedparser`](https://github.com/kurtmckee/feedparser) — de-facto standard, handles RSS 0.9x/1.0/2.0, Atom, JSON Feed, malformed XML. Actively maintained by kurtmckee. For speed-critical use, [`fastfeedparser`](https://github.com/kagisearch/fastfeedparser) from Kagi is a drop-in alternative.
- **Node:** [`rss-parser`](https://github.com/rbren/rss-parser) — small, promise-based, widely used.

Neither does deduplication; that's on you (see §2.5).

### 2.4 Full-article extraction

Publisher RSS usually gives title + summary, not the full body. To render real newspaper-like articles you need to fetch and extract the main content from the article page.

| Library | Language | Maintained? | Notes |
|---|---|---|---|
| [`trafilatura`](https://github.com/adbar/trafilatura) | Python | Yes (regular releases) | Best overall on benchmarks; used by HuggingFace/IBM/Microsoft; also handles feeds |
| [`readability-lxml`](https://github.com/buriy/python-readability) | Python | Yes | Mozilla Readability port; high median accuracy |
| [`newspaper3k`](https://github.com/codelucas/newspaper) | Python | **No release since 2018** | Popular but stale — web markup has drifted since |
| [`newspaper4k`](https://github.com/AndyTheFactory/newspaper4k) | Python | Yes | Actively maintained successor fork |
| [`@mozilla/readability`](https://github.com/mozilla/readability) | JS | Yes | Ships in Firefox Reader View |
| [Postlight Parser / Mercury](https://github.com/postlight/parser) | JS | Archived 2023 | Historically a good option; mirror-only now |

Benchmarks: trafilatura tops the [SIGIR/scrapinghub extraction benchmark](https://github.com/scrapinghub/article-extraction-benchmark) on mean F1 (~0.88–0.94). readability-lxml has the highest median; they complement each other.

**Recommendation:** trafilatura as primary, readability-lxml as fallback. Skip newspaper3k in favor of newspaper4k if you want author/date extraction too.

### 2.5 Deduplication

The same wire story appears on many outlets. Dedupe with a small pipeline:

1. Normalize URL (strip UTM params, fragment, lowercase host).
2. Hash the normalized URL — catches exact re-posts.
3. Title similarity: `rapidfuzz.fuzz.token_set_ratio(a, b) > 90` or MinHash over title shingles.
4. Optional: body shingling / SimHash for syndicated AP/Reuters reprints.

Libraries: [`rapidfuzz`](https://github.com/maxbachmann/RapidFuzz) (fast Levenshtein), `datasketch` (MinHash/LSH). Keep a small SQLite "seen" store so re-runs don't re-include yesterday's stories.

### 2.6 Paywalls and ethics

- **Respect `robots.txt`** and site ToS. `trafilatura` has helpers for this.
- **Don't strip paywalls.** If a feed's body is gated, keep the summary only and link out.
- Set a real `User-Agent` identifying the tool (e.g. `renewsable/0.1 (+https://github.com/...)`) so sites can block you cleanly if they want to.
- Rate-limit (1–2 req/sec per host) and cache with conditional GET (`If-Modified-Since`, `ETag`).
- This is personal use, one doc per day — the footprint is tiny if you behave.

### 2.7 A reasonable default feed set

User should be able to override any of these. Suggested starter list:

- BBC — `http://feeds.bbci.co.uk/news/world/rss.xml`
- Reuters World — via [reutersagency.com/feed/](https://www.reutersagency.com/feed/) (Reuters killed their public RSS in 2020; use aggregator feeds or [feedburner mirrors](https://feeds.feedburner.com/reuters/topNews))
- AP — `https://apnews.com/hub/ap-top-news.rss` (intermittent) or AP's site JSON
- NYT — `https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml`
- The Guardian — `https://www.theguardian.com/international/rss`
- The Economist — `https://www.economist.com/the-world-this-week/rss.xml`
- Financial Times — `https://www.ft.com/rss/home`
- Ars Technica — `http://feeds.arstechnica.com/arstechnica/index`
- Hacker News front page — `https://hnrss.org/frontpage`
- Local paper — user-configurable
- Weather — openweather / NWS, not a "feed" but a daily API call

This is a sensible default, but the config file should be the source of truth.

---

## 3. Document generation

### 3.1 Choosing format: PDF vs EPUB

| Dimension | PDF | EPUB |
|---|---|---|
| Newspaper-like layout (columns, headers, front page) | Excellent — fixed layout | Poor — reflowable |
| Font size/reflow on device | Fixed | reMarkable controls it |
| Annotation (pen on device) | Great | Good but page-less notes |
| File size | Larger | Smaller |
| Image handling on Paper Pro color screen | Full color | Full color |
| Typical "newspaper" feel | Yes | No — feels like a book |

**Verdict: PDF.** A newspaper is a visual artifact. PDF with a Paged Media CSS stylesheet gives you a masthead, dateline, multi-column body, page numbers, and fixed typography — the exact "stop scrolling" feel that justifies this project. EPUB is the right answer if you ever want a "morning book" instead of a "morning paper".

### 3.2 PDF tooling

| Tool | Approach | Strengths | Weaknesses |
|---|---|---|---|
| [WeasyPrint](https://weasyprint.org/) | HTML/CSS → PDF, Python | Excellent Paged Media support, actively developed, permissive license, great for newspaper layouts | Multi-column has [known limits](https://github.com/Kozea/WeasyPrint/issues/60): no column-span, limited column-break control |
| [Prince XML](https://www.princexml.com/) | HTML/CSS → PDF | Best-in-class Paged Media, beautiful output | Commercial (~$500/yr for personal), closed source |
| [Paged.js](https://pagedjs.org/) | HTML/CSS → PDF via headless Chrome | True browser rendering, pagedjs polyfill for CSS Paged Media | Needs Chrome, heavier runtime |
| [wkhtmltopdf](https://wkhtmltopdf.org/) | HTML/CSS → PDF via QtWebKit | Simple CLI | Deprecated upstream, old WebKit, no Paged Media |
| [ReportLab](https://www.reportlab.com/opensource/) | Programmatic Python | Total control | You're writing layout by hand — a lot of work for a newspaper |
| [LaTeX](https://www.latex-project.org/) (+ [`newspaper.sty`](https://ctan.org/pkg/newspaper)) | Typesetting | Best typography on the planet | Steep learning curve; newspaper templates are dated |
| [Pandoc](https://pandoc.org/) | Markdown/HTML → PDF via LaTeX/ConTeXt | Great for book-like output | Not ideal for multi-column newspaper layouts |
| **Calibre `ebook-convert`** | Recipe → PDF/EPUB | Built-in [news recipes](https://manual.calibre-ebook.com/news.html), periodical formatting, handles 1000+ sources out of the box | Output is "magazine" style, not "broadsheet"; hard to deeply customize without learning Calibre's recipe API |

**Calibre deserves special attention.** `ebook-convert recipe.recipe output.pdf` produces a properly-paginated periodical with TOC, sections, and masthead. Calibre ships built-in recipes for hundreds of outlets (NYT, Guardian, BBC, Economist, etc.) — you inherit years of per-site extraction work for free. Recipes are [Python classes](https://manual.calibre-ebook.com/news_recipe.html) subclassing `BasicNewsRecipe`. This is almost certainly the shortest path to a real MVP.

For a hand-rolled path, **WeasyPrint** is the right choice — open source, Python-native, good CSS coverage.

### 3.3 CSS Paged Media for a newspaper (WeasyPrint)

Minimal sketch you would build on:

```css
@page {
  size: 8.5in 11in;               /* slightly under rM Paper Pro; fits rM 2 well */
  margin: 0.6in 0.5in 0.7in 0.5in;
  @top-center   { content: "renewsable — " string(issue-date); font-family: "Source Serif Pro"; font-size: 9pt; }
  @bottom-right { content: counter(page) " / " counter(pages); font-size: 9pt; }
}
@page :first { @top-center { content: none; } }   /* clean masthead page */

body { font-family: "Source Serif Pro", Georgia, serif; font-size: 11pt; line-height: 1.35; }
h1.masthead { font-family: "Playfair Display", serif; font-size: 56pt; text-align: center; border-bottom: 3px double #000; }
.dateline { string-set: issue-date content(); text-align: center; font-variant: small-caps; }

article { column-count: 2; column-gap: 0.3in; column-rule: 1px solid #ddd; }
article h2 { column-span: all; font-size: 18pt; margin-top: 1em; }  /* note WeasyPrint limitation */
article p  { text-align: justify; hyphens: auto; orphans: 3; widows: 3; }
.byline { font-style: italic; font-size: 9.5pt; color: #444; }
```

WeasyPrint caveats to design around:
- `column-span: all` and per-column breaks are unreliable — use wrapper divs and `break-before: column`.
- Render at the target's aspect ratio (4:3 for Paper Pro, ~4:3 for rM 2 too).

### 3.4 Typography for e-ink

- **Serif for body**, at 11–13pt at typical print size. Georgia, Literata, Source Serif Pro, Charter, Bookerly-like. ([Kitaboo guide](https://kitaboo.com/best-fonts-for-ebooks/), [pdf.net e-book fonts](https://pdf.net/blog/best-e-book-fonts))
- Generous **line height** (1.3–1.45). E-ink "blurs" fine horizontal spacing less than backlit screens, so don't over-tighten.
- Hinted fonts beat variable fonts on e-ink. Prefer the hinted TTFs.
- **Color** on Paper Pro is limited (Gallery 3 e-ink — muted palette). Design for monochrome first; color is a nice-to-have.
- Avoid hairline strokes — they ghost on e-ink. Weight 400 minimum for body.

### 3.5 EPUB generation (if ever wanted)

- Python: [`ebooklib`](https://github.com/aerkalov/ebooklib) — EPUB2/3, full programmatic control (spine, TOC, CSS, metadata).
- Pandoc: `pandoc articles.md -o paper.epub --metadata title=...` — best if you already have Markdown.
- Calibre's `ebook-convert` can emit EPUB from the same recipes it uses for PDF.

### 3.6 Prior art for "RSS → newspaper PDF"

| Project | Language | Stars | Status | Notes |
|---|---|---|---|---|
| [j6k4m8/goosepaper](https://github.com/j6k4m8/goosepaper) | Python | ~318 | Active (last release Feb 2024) | **Directly addresses this problem.** Uses WeasyPrint; RSS/Mastodon/Reddit/Wikipedia/weather providers; JSON config; optional `--upload` via `~/.rmapy` |
| [bookfere/Calibre-News-Delivery](https://github.com/bookfere/Calibre-News-Delivery) | GH Actions + Calibre | — | Active | Template repo: scheduled Calibre news builds + email delivery. Good pattern to steal |
| [eksubin/Remarkable-RSS-Feed](https://github.com/eksubin/Remarkable-RSS-Feed) | Python | Small | — | RSS → PDF → Google Drive (not direct upload) |
| [leandertolksdorf/remarkable-rss](https://github.com/leandertolksdorf/remarkable-rss) | TS/Node | Small | — | NextJS + Mongo + cron, syncs RSS to rM cloud |
| [ec1oud/remarkable-rss](https://github.com/ec1oud/remarkable-rss) | — | Small | — | Alternative RSS-on-rM approach |

`goosepaper` is the closest existing solution; worth reading thoroughly even if not adopted wholesale. See [reHackable/awesome-reMarkable](https://github.com/reHackable/awesome-reMarkable) for more.

---

## 4. Uploading to reMarkable

### 4.1 The API situation

There is **no officially public, documented reMarkable Cloud API**. reMarkable runs a [Developer Portal](https://developer.remarkable.com/) focused on connected-service integrations, but raw cloud sync is not a supported public surface. The community has reverse-engineered it — see [akeil.de: reMarkable Cloud API](https://akeil.de/posts/remarkable-cloud-api/) and [splitbrain/ReMarkableAPI](https://github.com/splitbrain/ReMarkableAPI) for protocol docs.

Auth flow used by every community client:

1. User visits **https://my.remarkable.com/device/desktop/connect** while logged in → gets an 8-char one-time code.
2. Client POSTs `{ code, deviceDesc, deviceID }` to the auth endpoint → receives a long-lived **device token**.
3. On each session, client exchanges device token for a short-lived **user token** (JWT).
4. Document upload uses the user token; sync protocol has gone through revisions, latest being "sync15". ([support article on one-time codes](https://support.remarkable.com/s/article/Help-using-one-time-codes))

### 4.2 Community clients

| Tool | Language | Stars | Status | Notes |
|---|---|---|---|---|
| [ddvk/rmapi](https://github.com/ddvk/rmapi) | Go | ~238 | **Actively maintained, v0.0.32 Nov 2025** | De-facto standard CLI; supports sync15; Homebrew `brew install io41/tap/rmapi` |
| [juruen/rmapi](https://github.com/juruen/rmapi) | Go | — | Archived upstream | Original; superseded by ddvk fork |
| [subutux/rmapy](https://github.com/subutux/rmapy) | Python | — | Last commit May 2024 (stale-ish) | Synchronous Python client; goosepaper uses `~/.rmapy` config |
| [rschroll/rmcl](https://github.com/rschroll/rmcl) | Python (async) | — | Fork of rmapy going its own way | async/await API |
| [hugodecasta/remarkable-cloud-js](https://github.com/hugodecasta/remarkable-cloud-js) | Node | — | — | JS client |
| [peerdavid/remapy](https://github.com/peerdavid/remapy) | Python | — | — | GUI explorer, also a library |
| [remailable/remailable](https://github.com/remailable/remailable) | Hosted | — | Service | Email a PDF, it lands on your rM |
| [PaulKinlan/send-to-remarkable](https://github.com/PaulKinlan/send-to-remarkable) | Node | — | — | Self-hostable email → rM bridge |

**rmapi (ddvk fork) is the right default.** CLI, Go binary, actively maintained, handles the current sync protocol, scriptable.

### 4.3 Example rmapi usage

```bash
# One-time auth: prompts for code from my.remarkable.com/device/desktop/connect
rmapi                      # launches interactive shell on first run; enter the 8-char code

# Non-interactive upload to a folder (creates the folder if missing)
rmapi mkdir /News || true
rmapi put ./renewsable-2026-04-18.pdf /News/
rmapi ls /News

# Optional: --coverpage or --content-only for re-uploads
rmapi put --force ./renewsable-2026-04-18.pdf /News/
```

Auth state lives in `~/.rmapi` (or `$RMAPI_CONFIG`). Back this file up — re-auth requires physical interaction.

### 4.4 Alternatives to direct cloud upload

- **Email bridge.** [remailable](https://github.com/remailable/remailable) or self-hosted [send-to-remarkable](https://github.com/PaulKinlan/send-to-remarkable). Reliable but adds a hop.
- **USB/SSH.** Enable developer mode, `scp` PDFs into `/home/root/.local/share/remarkable/xochitl/` with a companion `.metadata` JSON. Cumbersome and device-tethered; not suitable for a daily cron job.
- **Read on reMarkable browser extension.** Manual; not scriptable.
- **WebDAV sync** (community tool [remarkdav](https://github.com/rHackable/remarkdav)) or **Google Drive sync**. Extra service dependency; slower feedback loop.

Cloud via rmapi wins on simplicity for a daily automated job.

---

## 5. Scheduling on macOS

### 5.1 Options

| Option | Pros | Cons |
|---|---|---|
| **launchd** (`~/Library/LaunchAgents/*.plist`) | Native, runs at wake if missed during sleep, only requires user session | Plist XML is verbose |
| `cron` (user crontab) | Familiar | **Skips** runs if Mac is asleep at fire time; deprecated on macOS |
| `at` | Dead simple one-shots | Not recurring |
| GitHub Actions scheduled workflow | Runs even if your Mac is off; free for public repos | Requires storing rmapi auth token as a secret; 1500 min/mo free for private |
| Self-hosted VPS + cron | Always-on | Costs money; another machine to maintain |
| Raspberry Pi on your LAN | Cheap, always-on | Hardware to manage |

Since the user is on macOS and the Mac is probably asleep at 5am but wakes sometime in the morning, **launchd with `StartCalendarInterval`** is the right answer for local runs. It fires at the scheduled time or on the next wake after. ([launchd.info tutorial](https://www.launchd.info/), [alvinalexander launchd examples](https://alvinalexander.com/mac-os-x/launchd-plist-examples-startinterval-startcalendarinterval/))

If you want the paper to be *there* when you pick up the tablet rather than *eventually today*, run it in the cloud (GitHub Actions) and skip the local-sleep problem entirely.

### 5.2 Example launchd plist

`~/Library/LaunchAgents/com.bnc.renewsable.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>                 <string>com.bnc.renewsable</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>/usr/local/bin/caffeinate -i /Users/bnc/projects/renewsable/bin/run.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>   <integer>5</integer>
    <key>Minute</key> <integer>30</integer>
  </dict>
  <key>RunAtLoad</key>             <false/>
  <key>StandardOutPath</key>       <string>/Users/bnc/Library/Logs/renewsable.out.log</string>
  <key>StandardErrorPath</key>     <string>/Users/bnc/Library/Logs/renewsable.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key> <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
```

Load with `launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.bnc.renewsable.plist`. Test immediately with `launchctl kickstart gui/$UID/com.bnc.renewsable`.

Notes:
- `caffeinate -i` keeps the CPU awake for the duration of the script so the run completes even on battery.
- If the Mac is asleep at 5:30 AM, launchd runs the job at the next wake. That's usually fine — you get the paper when you open the lid.
- To wake the Mac specifically for this, use `pmset repeat wakeorpoweron MTWRFSU 05:25:00` alongside the plist.
- `StartCalendarInterval` under `WakeSystem` (newer launchd) also exists but is less reliably documented.

### 5.3 GitHub Actions alternative

```yaml
name: daily-paper
on:
  schedule: [{ cron: '30 4 * * *' }]   # 04:30 UTC
  workflow_dispatch:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -r requirements.txt
      - run: python -m renewsable build --out paper.pdf
      - name: Install rmapi
        run: |
          curl -L https://github.com/ddvk/rmapi/releases/latest/download/rmapi-linuxx86-64.tar.gz | tar xz
          mkdir -p ~/.config/rmapi
          echo "$RMAPI_CONFIG" > ~/.config/rmapi/rmapi.conf
        env: { RMAPI_CONFIG: ${{ secrets.RMAPI_CONFIG }} }
      - run: ./rmapi put paper.pdf /News/
```

The auth token file goes into a repo secret. Precisely because it's a token, rotate by re-pairing if it ever leaks.

---

## 6. Architecture options

### Option A — "Stand on Calibre's shoulders"

```
ebook-convert <recipe>.recipe paper.pdf  →  rmapi put paper.pdf /News/
launchd plist kicks it off at 05:30 daily
```

- **Deps:** Calibre (GUI app, `brew install --cask calibre`), `rmapi` (`brew install io41/tap/rmapi`), a shell script. Optionally a custom recipe if the built-ins don't cover your outlet.
- **Effort:** 1 evening for MVP. Recipes already exist for most big outlets.
- **Maintenance:** Very low. Calibre devs maintain the recipes; you get updates for free with `brew upgrade --cask calibre`.
- **Customizability:** Medium. Layout is Calibre's periodical style (decent but generic). Deep customization means learning the [`BasicNewsRecipe` API](https://manual.calibre-ebook.com/news_recipe.html).
- **Risks:** Ties you to Calibre. Output looks like "a Calibre book", not a designed broadsheet.

### Option B — "Custom Python pipeline"

```
feedparser  →  trafilatura (full text)  →  rapidfuzz dedupe  →  Jinja2 HTML template
             →  WeasyPrint → paper.pdf  →  rmapi put paper.pdf /News/
```

- **Deps:** `feedparser`, `trafilatura`, `readability-lxml` (fallback), `rapidfuzz`, `jinja2`, `weasyprint` (+ Cairo/Pango/GDK-Pixbuf via Homebrew), `rmapi`.
- **Effort:** 2–4 evenings for a pleasant MVP. Real time sinks: typography, column breaks, image handling.
- **Maintenance:** Medium. You own the layout, the feed list, the dedupe logic. Feed sources break occasionally.
- **Customizability:** High. Full CSS control, your own masthead, whatever providers you want.
- **Risks:** WeasyPrint multi-column quirks; you will care about orphans/widows eventually.

Effectively this is what `goosepaper` already does. **Forking goosepaper is a faster variant of Option B.**

### Option C — "Node.js equivalent"

```
rss-parser  →  @mozilla/readability (+ jsdom)  →  Handlebars HTML
            →  Paged.js + Puppeteer → paper.pdf  →  rmapi (still Go) put
```

- **Deps:** `rss-parser`, `@mozilla/readability`, `jsdom`, `puppeteer`, `pagedjs-cli`, `rmapi`.
- **Effort:** 2–4 evenings. Paged.js + headless Chrome gives the most CSS-spec-accurate output.
- **Maintenance:** Medium. Puppeteer/Chrome upgrades occasionally break things.
- **Customizability:** High. Paged.js is arguably the best open CSS Paged Media engine.
- **Risks:** Heaviest runtime (bundles a Chromium). Overkill for a daily cron job.

### Quick comparison

| Axis | A (Calibre) | B (Python custom) | C (Node custom) |
|---|---|---|---|
| Time to MVP | ★★★★★ | ★★★ | ★★ |
| Layout quality / "newspaper" feel | ★★★ | ★★★★ | ★★★★★ |
| Customizability | ★★ | ★★★★★ | ★★★★★ |
| Maintenance burden | ★★★★★ (low) | ★★★ | ★★ |
| Runtime footprint | medium (Calibre) | small | large (Chromium) |
| Leverages prior art | Yes (1000+ recipes) | Yes (goosepaper) | Mostly DIY |

---

## 7. Recommended approach

For a solo developer on macOS targeting rM 2 / Paper Pro, **hybrid of A and B**, starting from goosepaper:

1. **MVP (day 1–2).** Install `goosepaper` and `rmapi`. Write a config file with ~6 RSS feeds. Wire a launchd plist that runs `goosepaper --upload` daily at 05:30. You now have a working daily paper. This is the smallest artifact that proves the pipeline end-to-end and gets you reading on the device tomorrow morning.

2. **Polish (week 1).** Fork goosepaper (or build a thin wrapper) to:
   - customize the masthead / front page,
   - add a deduplication pass with `rapidfuzz`,
   - add a "top stories" front page provider using the Guardian API for full body text,
   - set page size to 8.5" × 11.33" (4:3) to match Paper Pro proportions,
   - pick typography: Source Serif Pro body @ 11pt, Playfair Display masthead.

3. **Harden (week 2+).** Add:
   - a "seen" SQLite store so stories don't reappear,
   - retry/backoff per feed, graceful degradation when a feed is down,
   - log rotation and a weekly self-ping email if the job fails N days in a row,
   - optional move: port scheduling to GitHub Actions so the paper lands even when the Mac is off.

4. **Don't bother with (early).** A custom CSS multi-column broadsheet. Calibre-style recipes for every obscure outlet. EPUB output. A web UI. Color-specific Paper Pro tuning. These are all fine once the daily-run loop is boringly reliable.

**MVP checklist:**
- [ ] `brew install --cask calibre` (optional fallback)
- [ ] `brew install io41/tap/rmapi`; run `rmapi` once to pair (grab code from [my.remarkable.com/device/desktop/connect](https://my.remarkable.com/device/desktop/connect))
- [ ] `pip install goosepaper` in a dedicated virtualenv at `~/projects/renewsable/.venv`
- [ ] `goosepaper.json` with your feeds
- [ ] `bin/run.sh` that activates the venv, runs goosepaper, then `rmapi put` into `/News/`
- [ ] `~/Library/LaunchAgents/com.bnc.renewsable.plist` loaded
- [ ] `pmset repeat wakeorpoweron MTWRFSU 05:25:00`
- [ ] Verify tomorrow morning that a dated PDF appears on the tablet

After one week of reliable morning delivery, make the decision whether to invest in a custom layout pipeline. By then you'll know which feeds you actually read, which ones produce garbage, and which sections you wish were on the front page — all of which are inputs you don't have today.

---

## 8. References

### reMarkable
- [reMarkable Paper Pro — features](https://remarkable.com/products/remarkable-paper/pro/details/features)
- [Paper Pro vs reMarkable 2](https://remarkable.com/blog/remarkable-paper-pro-vs-remarkable-2-what-s-the-difference)
- [reMarkable Developer Portal](https://developer.remarkable.com/)
- [One-time codes support article](https://support.remarkable.com/s/article/Help-using-one-time-codes)
- [akeil.de — reMarkable Cloud API](https://akeil.de/posts/remarkable-cloud-api/)
- [splitbrain/ReMarkableAPI (protocol docs)](https://github.com/splitbrain/ReMarkableAPI)
- [reHackable/awesome-reMarkable](https://github.com/reHackable/awesome-reMarkable)

### reMarkable clients
- [ddvk/rmapi (Go, maintained)](https://github.com/ddvk/rmapi)
- [juruen/rmapi (archived original)](https://github.com/juruen/rmapi)
- [subutux/rmapy (Python)](https://github.com/subutux/rmapy)
- [rschroll/rmcl (async Python)](https://github.com/rschroll/rmcl)
- [hugodecasta/remarkable-cloud-js](https://github.com/hugodecasta/remarkable-cloud-js)
- [peerdavid/remapy](https://github.com/peerdavid/remapy)
- [remailable/remailable](https://github.com/remailable/remailable)
- [PaulKinlan/send-to-remarkable](https://github.com/PaulKinlan/send-to-remarkable)

### News collection
- [kurtmckee/feedparser](https://github.com/kurtmckee/feedparser)
- [kagisearch/fastfeedparser](https://github.com/kagisearch/fastfeedparser)
- [rbren/rss-parser (Node)](https://github.com/rbren/rss-parser)
- [adbar/trafilatura](https://github.com/adbar/trafilatura)
- [buriy/python-readability](https://github.com/buriy/python-readability)
- [codelucas/newspaper (newspaper3k, stale)](https://github.com/codelucas/newspaper)
- [AndyTheFactory/newspaper4k](https://github.com/AndyTheFactory/newspaper4k)
- [mozilla/readability (JS)](https://github.com/mozilla/readability)
- [scrapinghub/article-extraction-benchmark](https://github.com/scrapinghub/article-extraction-benchmark)
- [NewsAPI](https://newsapi.org), [GNews](https://gnews.io), [Guardian Open Platform](https://open-platform.theguardian.com/), [NYT Developer](https://developer.nytimes.com/), [Mediastack](https://mediastack.com), [NewsData.io](https://newsdata.io)

### Document generation
- [WeasyPrint](https://weasyprint.org/) — [docs](https://doc.courtbouillon.org/weasyprint/stable/), [multi-column issue #60](https://github.com/Kozea/WeasyPrint/issues/60)
- [Paged.js](https://pagedjs.org/)
- [Prince XML](https://www.princexml.com/)
- [ReportLab](https://www.reportlab.com/opensource/)
- [Pandoc — EPUB](https://pandoc.org/epub.html)
- [aerkalov/ebooklib](https://github.com/aerkalov/ebooklib)
- [Calibre news recipe manual](https://manual.calibre-ebook.com/news.html), [recipe API](https://manual.calibre-ebook.com/news_recipe.html)
- [j6k4m8/goosepaper](https://github.com/j6k4m8/goosepaper)
- [bookfere/Calibre-News-Delivery](https://github.com/bookfere/Calibre-News-Delivery)
- [eksubin/Remarkable-RSS-Feed](https://github.com/eksubin/Remarkable-RSS-Feed)

### Typography for e-ink
- [Kitaboo — best fonts for ebooks](https://kitaboo.com/best-fonts-for-ebooks/)
- [pdf.net — best e-book fonts](https://pdf.net/blog/best-e-book-fonts)
- [EditionGuard — fonts for ebooks](https://www.editionguard.com/learn/best-fonts-e-books/)
- [nicoverbruggen/ebook-fonts](https://github.com/nicoverbruggen/ebook-fonts)

### Scheduling on macOS
- [launchd.info tutorial](https://www.launchd.info/)
- [Alvin Alexander — launchd examples](https://alvinalexander.com/mac-os-x/launchd-plist-examples-startinterval-startcalendarinterval/)
- [Scheduling a cron job on macOS with wake support](https://deniapps.com/blog/scheduling-a-cron-job-on-macos-with-wake-support)
- [Darnell — launchd automation](https://blog.darnell.io/automation-on-macos-with-launchctl/)
