# Handoff — MARC Serials Toolkit

Written 5 August 2026, moving development from Windows to macOS.

## Where things stand

**Everything is committed and pushed.** `main` is at `368b287` ("Adding
changelog and version information"), the working tree is clean, and local is
neither ahead of nor behind `origin/main`. There is no work-in-progress to
recover — a clone gets you the exact state development stopped at.

Current version is **0.5.0**, recorded in `shared/about.json`.

## Getting running on the Mac

```bash
git clone https://github.com/bdmcodey/marc-serials-toolkit.git
cd marc-serials-toolkit
python3 -m venv .venv && source .venv/bin/activate
pip install -r converter/requirements.txt
```

Both apps share the same two dependencies, so one install covers both. Verified
against Python 3.13.1, Flask 3.1.2, pymarc 5.2.3.

Run each app from **its own directory** — they resolve templates and the shared
stylesheet relative to their own file location:

```bash
cd converter && python3 app.py
```

```bash
cd pattern-detector && python3 app.py
```

Converter is on <http://localhost:5000>, detector on <http://localhost:5001>.
They are independent Flask apps and can run at the same time.

## Things that will bite you on the Mac

**The test MARC files are not in the repo.** `.gitignore` excludes `*.mrc`
except `data/example_holdings.mrc`, deliberately, so real holdings data stays out
of version control. Everything was tested against files on the USC share:

| file | size | character |
|---|---|---|
| `test_extract_10per.mrc` | 19,068 bytes | well-formed, enumeration-first |
| `TEST_50records_0615_853-1.mrc` | 21,923 bytes | unkempt, year-first, already has 853s |

They live at
`//mongo.usc.edu/rfolders/codey/Desktop/Projects/serials-enhancement/`. On macOS
mount the share first, after which paths become `/Volumes/...`:

```bash
open 'smb://mongo.usc.edu/rfolders'
```

Any script carrying the Windows UNC path needs that prefix swapped.

**Template edits need a restart.** Neither app runs in debug mode, so Jinja
caches compiled templates — editing `index.html` or `tool.html` and reloading
shows the *old* page. Either restart, or run with `FLASK_DEBUG=1 python3 app.py`.
`shared/ui.css` is served as a file and needs no restart, though the browser may
cache it, so hard-reload.

**Orphaned servers hold the port.** This cost real time on Windows: a stopped
process sometimes kept running and kept answering, serving a stale template. On
macOS:

```bash
lsof -ti:5000 | xargs kill -9
```

If an edit "doesn't take effect", check for a second process before you start
debugging the code.

**Line endings.** Files in the repo are LF. Git on Windows warned about CRLF
conversion on every write; that disappears on macOS. Leave `core.autocrlf` unset.

## Layout

```
converter/            866 -> 853/863 converter (Flask, port 5000)
  app.py                routes: upload, preview, convert, batch, download
  holdings_parser.py    866 text -> structured ranges. TWO grammars:
                        enumeration-first (v.1:no.2(1990)) and a separate
                        chronology-first "block" grammar (1993: (1 [Feb]))
  marc_converter.py     ranges -> 853/863; convention resolution;
                        convert_record() decides $8 linking across a record
  templates/index.html  UI: markup, app-specific CSS, all JS

pattern-detector/     866 -> regex clusterer (Flask, port 5001)
  app.py                routes: detect, upload, test-regex
  pattern_detector.py   tokenise, cluster by signature, build regexes
  templates/tool.html   UI

shared/               served by BOTH apps
  ui.css                design tokens, themes, components, layout
  about.json            version + plain-language changelog (single source)

deploy/               systemd units + nginx config for tools.matthewcodey.com
data/                 synthetic sample only
ai-regex/             experimental LLM regex generation, not part of the web tools
pnx-lookup/           separate local tool, untouched this session
```

The two web apps duplicate a little presentational markup — the about-dialog
block — because they have separate `template_folder`s. Content stays
single-sourced in `shared/about.json`; only about twenty lines of Jinja repeat.
That was a deliberate call over introducing a shared template path.

## Calibrated values — do not change casually

**`MAX_PATTERN_TOKENS = 40`** in `pattern_detector.py`. Clusters longer than this
are reported as "too idiosyncratic to express as a pattern" instead of
generating a regex. Calibrated against both MARC files: real statements cost
15–45 regex characters per token, so above roughly 45 tokens the detector emits
regexes its own Test button rejects at 2,000 characters. At 40 the longest regex
observed was 1,506.

**`_ALLOWED_SUBFIELDS = "a".."m"`** in `marc_converter.py`. An 853 carries
captions in `$a`–`$h` for enumeration and `$i`–`$m` for chronology. Everything a
convention must not touch — `$8` linking, `$u`, `$v`, `$w`, `$x`–`$z` — falls
outside that range, so a single allowlist covers the whole rule.

**Conversion conventions.** `standard` follows MARC 21; `house` reproduces the
local practice found in existing records (year in `$a`, chronology as text). Both
are starting points: the UI lets the cataloger override every subfield, and
`resolve_convention()` rejects invalid or colliding codes back to the preset
rather than writing them into records.

## No automated test suite

This is the biggest gap. All verification was ad-hoc probe scripts, run once and
discarded. If you touch parsing or field generation, re-check these by hand —
they held consistently, and any deviation means something broke.

| check | expected |
|---|---|
| converter, well-formed file, standard | 38 853s / 114 863s |
| converter, unkempt file, house | 52 853s / 166 863s |
| `$8` integrity, both files | no duplicate 853 `$8`, no orphaned 863, idempotent across repeated runs |
| parse rate, unkempt | 48 / 51 |
| parse rate, well-formed | 114 / 116 |
| detector, all three files | 100% match on every cluster that generates a regex |
| detector, max regex length | 2,000 characters or fewer |

Turning these into a real test file is the single highest-value next task.

## Known loose ends

- **`gunicorn` is in neither `requirements.txt`**, though both systemd units
  depend on it. `deploy/README.md` installs it by hand.
- **`split_multi_range()`** in the detector splits only on `,` and `;` at paren
  depth 0, so a long statement using another separator (` / `) arrives whole and
  can trip the token guard. This is the upstream cause of most "too
  idiosyncratic" flags.
- **Detector and converter are not integrated.** Investigated and deliberately
  not done: the detector's generated regexes cannot drive the converter, because
  on irregular data its capture groups are positional mush (`start_num_7`) with
  no semantics. A shared ingest plus detector-as-triage is the version worth
  building — not regex-driven parsing.
- **The record list shows only two or three records at a time** at 1280×900, a
  consequence of accessible sizing (44px targets, 18px body). Deliberate; worth
  revisiting with the cataloger in front of it.
- **The version-badge footer costs about 54px** of vertical space, 70px at the
  largest text setting. Moving the badge into the existing header would reclaim
  it if that trade turns out to be wrong.
- **Licensing is unresolved.** MIT was withdrawn pending institutional IP review;
  see `NOTICE.md`. No license is currently granted, and the intent is
  AGPL-3.0-or-later. Note that the git history still contains the old MIT
  `LICENSE` file — deleting it did not remove it from earlier commits, which
  `NOTICE.md` states plainly.

## Deployment

Not a long-term hosted service — this is a demonstration. The existing setup on
`tools.matthewcodey.com` is two gunicorn services behind nginx:

```bash
cd ~/marc-serials-toolkit && git pull && sudo systemctl restart mcsite-converter mcsite-patterns
```

nginx proxies `/converter/` to `127.0.0.1:8001` and `/patterns/` to `:8002`, and
the trailing-slash `proxy_pass` strips the path prefix. **Templates therefore use
relative URLs** (`api/detect`, `ui.css`) — an absolute `/ui.css` would break
behind the proxy. Keep that convention.

## Keeping the changelog current

`shared/about.json` is read per request, so editing it and reloading is enough —
no restart needed. Add a new entry at the top of `changelog`, bump `version`, and
write for catalogers: say what changed about their output or their screen, not
what changed in the code. The existing entries are the model.

This matters more than it looks. The output has already changed materially
between versions — 0.5.0 took one file from 113 generated 853s down to 38, and
0.3.0 took another from 6% to 94% parseable. The changelog is how a cataloger
finds out why this week's results differ from last week's.
