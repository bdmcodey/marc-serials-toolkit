# Handoff — MARC Serials Toolkit

Rewritten 2 September 2026, at the point where Workbench UI work becomes the
main thread. Supersedes the August handoff, which covered moving development
from Windows to macOS; the operational sections that still apply are kept below.

## Where things stand

`main` is at **`ed46d33`** ("Make enumeration an ordered hierarchy of any depth
(D6, D8) (#16)"), version **0.7.0**, everything merged and pushed. The suite is
**348 passed, 8 skipped, 2 xfailed** in about a second.

```bash
python -m pytest                       # 348 green
python scripts/corpus_report.py        # the corpus summary
python scripts/corpus_report.py --drift   # only tags that no longer hold
```

`--drift` should say *"No drift: every tag in the corpus still describes what
the tools do."* If it does not, the last change moved something — read what it
says before assuming it is a regression. It is often a fix, and the corpus tag
is what needs updating.

**The three tools are joined up.** The Workbench (`workbench/`) is the one to
open first: it detects patterns over an uploaded `.mrc`, asks a cataloguer to
confirm what each captured value means, and converts with the answer. Statements
no confirmed pattern matches fall through to `holdings_parser.parse_866()`, so
an empty pattern library gives output byte-identical to the Converter's — pinned
in `tests/test_workbench_api.py`.

## Getting running

```bash
git clone https://github.com/bdmcodey/marc-serials-toolkit.git
cd marc-serials-toolkit
python3 -m venv .venv && source .venv/bin/activate
pip install -r converter/requirements.txt      # covers all three apps
pip install -r requirements-dev.txt            # pytest
```

Run each app from **its own directory** — they resolve templates and the shared
stylesheet relative to their own file location:

```bash
cd workbench        && python3 app.py    # 5003  <- start here
cd converter        && python3 app.py    # 5000
cd pattern-detector && python3 app.py    # 5001
```

`WORKBENCH_PORT`, `CONVERTER_PORT` and `DETECTOR_PORT` override the defaults.
They are named separately so all three can be exported at once. Deployment runs
under gunicorn and ignores them.

## Read these three files before changing anything

1. **`CORPUS-FINDINGS.md`** — the defect log D1–D18, what each one was, which
   version fixed it, and *why the fix took the shape it did*. Several sections
   record reasoning that later turned out to be wrong and say so. Do not
   re-litigate a fixed defect without reading its section; the obvious fix was
   usually tried and rejected for a reason stated there.
2. **`data/textual_holdings_corpus.txt`** — 112 real 866 `$a` statements
   transcribed from catalogue records, each tagged with the defect it exercises
   (`# warn:D2`, `# fail:D7`). The tags are load-bearing: `--drift` compares
   them against live behaviour, so a code change that alters an outcome shows up
   as a tag disagreement rather than needing to be remembered.
3. **`shared/about.json`** — the cataloguer-facing changelog, read per request
   so editing it needs no restart. Add a new entry at the top, bump `version`,
   and write about the *output or the screen*, never the code. Say what a
   cataloguer will see differently.

**Editing `about.json`:** insert the entry as text rather than round-tripping
through `json.dumps`. The file holds `\uXXXX` escapes (em dashes, curly quotes)
and a re-encode with `ensure_ascii=False` rewrites every one of them, burying a
one-entry change in a whole-file diff.

## What the Workbench looks like from the inside

```
workbench/
  app.py                12 JSON endpoints; the whole UI is one page driven by them
  pattern_bridge.py     a detector regex + a cataloguer's decisions -> ParseResult
  pattern_library.py    validating, storing, importing/exporting confirmed patterns
  templates/tool.html   ~2100 lines: markup, page CSS, all JS. Three <section
                        class="step"> panels — #step-source, #step-patterns,
                        #step-convert — revealed in order.
```

The join is `pattern_bridge.build_parse_result()`. Three rules in it are load
bearing and each has a comment saying why:

- **A pattern must `fullmatch` a segment.** A partial match is treated as no
  match at all. `re.search` let a pattern claim the tail of a statement and
  silently drop everything before it.
- **With the parser as fallback, an unmatched segment goes to `parse_866()`.**
  A pattern covering most of a statement must not cost the rest of it.
- **Without the fallback, it is all or nothing.** The 866 is deleted once
  anything is written from it, so converting half a statement deletes holdings.
  Half is the one outcome worse than none.

`GroupRole` is the unit of a cataloguer's decision: `group`, `boundary`
(start/end), `kind` (`enum` / `year` / `month` / `ignore` / `unresolved`), and —
for enumeration only — `level` and `caption`. `assign_levels()` numbers any
enumeration role whose level is `None` by the order it appears, so a screen can
leave the level alone and get the right answer.

## Decisions already made — do not quietly reverse these

**Enumeration is an ordered list of any depth, not volume-then-issue-then-part.**
MARC 21 puts 853 captions in `$a`–`$f` "in descending order of significance" and
says nothing about which words go in them. Position decides the subfield; the
caption is the word the statement used, kept as written. A serial numbered only
by issue quite properly gets `$a no.` This was D6, and the three-level model is
what made it unfixable.

**A caption word is a label, never a level.** `caption_slot()` answers only
"enumeration, year or month". If you find yourself writing a map from `no.` to
a subfield, stop — that is the bug that was just removed.

**One 853 governs every 863 linked to it.** Two statements on one record that
number by different hierarchies cannot share one, so a value whose level the
853 contradicts is left out and named rather than filed under the wrong caption.

**A number with no caption is not guessed at.** The `16` in `?: 16` carries
nothing to say which level it belongs to, so the parser holds the statement for
review rather than defaulting it to a volume. No amount of better parsing can
fix this — the information is not in the statement. The Workbench's confirm step
is the mechanism that *can*, because a cataloguer who knows the collection
supplies it once. The one exception is a captionless number sitting immediately
above a captioned issue (`39 no 1`), which is offered as a suggestion and still
has to be accepted.

**Second-level chronology that cannot be attributed to a boundary is dropped**,
not guessed at, in both directions. This is the cataloguer's own ruling and
matches how MARC-based systems read the fields.

**Silence must never mean success.** Every source token should end in exactly
one of three buckets: encoded, deliberately dropped with a reason on the record,
or unaccounted — and unaccounted must force review. This is the "bounded errors"
framework behind the conference talk. The corpus is currently at **one** silent
loss out of 112, down from 36. Protect that number above the clean-conversion
rate; statements have moved *out* of "clean" on purpose more than once.

## What is next on the Workbench

Three UI requests, in the order they were raised. None is started.

1. **A skip button** — remove a pattern or a record from conversion entirely,
   leaving it untouched. The record list already carries per-record state
   (`reviewed`, a `Set` in `tool.html`), so this is a third state alongside it,
   plus a filter chip in `#review-bar`.
2. **The pattern library needs to collapse.** With a large `.mrc` loaded it is
   unusable at length — scrolling past it to reach the next step is the whole
   interaction. Each pattern is a `.pattern-card` with a `.pc-toggle` that
   already opens and closes its body; what is missing is a collapse for the
   *section*, and probably a default of collapsed once past some count.
3. **Jump from a record in Convert back to its pattern**, unhiding the library
   if collapsed, so a mistake spotted during review can be fixed at its source.
   The hard half is the consequence: any record already marked reviewed that the
   edited pattern touches has to go back to unreviewed, or the review state
   quietly lies. `_source_labels()` and the `by_source` counts in
   `/api/batch-convert` already know which pattern read which statement.

### Still open in the defect log

- **D7** — genuinely captionless statements (`8,13,15,17,19,20-(1982-1994)`).
  Expected to keep failing in the parser; the confirm step is what could convert
  them, which makes them a Workbench test case rather than a parser bug.
- **D10, D11** — by design, documented as such. D10's run-on is the last
  remaining silent loss: `_bracket_chron_unit` drops day-level dates from it.
  D4's fix could be extended to cover it.
- **D14** — the detector and converter disagree about
  `8,13,15,17,19,20-(1982-1994)`. A design decision to make explicitly.
- Two `xfail` tests in `tests/test_holdings_parser.py` describe known defects and
  say what the behaviour should be — a spaced-slash separator, and a brace note
  defeating the block grammar. Fixing one turns its test green.

### One judgement call worth revisiting

The settings dialogs edit three enumeration levels while the standard convention
has six. Moving level 1 onto `$d` collides with the unedited fourth level; the
cataloguer's choice wins, the unseen level drops out, and the lost depth is
reported ("room for 5 levels, not 6"). Refusing the change instead is a one-line
switch in `resolve_convention()`.

## Things that will bite you

**Template edits need a restart.** None of the apps runs in debug mode, so Jinja
caches compiled templates — editing `tool.html` and reloading shows the *old*
page. Either restart, or run with `FLASK_DEBUG=1 python3 app.py`.
`shared/ui.css` is served as a file and needs no restart, though the browser may
cache it, so hard-reload.

**Orphaned servers hold the port.** A stopped process sometimes keeps running
and keeps answering, serving a stale template. If an edit "doesn't take effect",
check for a second process before you start debugging the code — but check the
process name first and only kill your own Python:

```bash
pkill -f 'python3 app.py'
```

**macOS AirPlay Receiver owns port 5000.** The listener is ControlCenter, a
system process — *do not* kill it. Confirm what you are looking at first:

```bash
lsof -i:5000 -sTCP:LISTEN -P
```

Either run the converter on another port with `CONVERTER_PORT`, or switch
AirPlay Receiver off in System Settings → General → AirDrop & Handoff.

**The private test MARC files are not in the repo.** `.gitignore` excludes
`*.mrc` except the two synthetic files in `data/`, deliberately, so real
holdings data stays out of version control.

| file | size | character |
|---|---|---|
| `test_extract_10per.mrc` | 19,068 bytes | well-formed, enumeration-first |
| `TEST_50records_0615_853-1.mrc` | 21,923 bytes | unkempt, year-first, already has 853s |

They live at
`//mongo.usc.edu/rfolders/codey/Desktop/Projects/serials-enhancement/`. On macOS
mount the share first (`open 'smb://mongo.usc.edu/rfolders'`), after which paths
become `/Volumes/...`. The calibration tests skip unless you point at them:

```bash
MARC_TEST_DATA_DIR=/path/to/mounted/share python -m pytest -m calibration
```

**Line endings.** Files in the repo are LF. Leave `core.autocrlf` unset.

## Calibrated values — do not change casually

**`MAX_PATTERN_TOKENS = 40`** in `pattern_detector.py`. Clusters longer than this
are reported as "too idiosyncratic to express as a pattern" instead of
generating a regex. Calibrated against both private MARC files: real statements
cost 15–45 regex characters per token, so above roughly 45 tokens the detector
emits regexes its own Test button rejects at 2,000 characters.

**`_ALLOWED_SUBFIELDS = "a".."m"`** in `marc_converter.py`. An 853 carries
captions in `$a`–`$h` for enumeration and `$i`–`$m` for chronology. Everything a
convention must not touch — `$8` linking, `$u`, `$v`, `$w`, `$x`–`$z` — falls
outside that range, so one allowlist covers the whole rule. `read_853_slots()`
and `read_853_captions()` filter by it too, so a `$w m` frequency code is not
read as a caption.

**Conversion conventions.** `standard` follows MARC 21; `house` reproduces the
local practice found in existing records (year in `$a`, chronology as text).
Both are starting points: every subfield stays editable, and
`resolve_convention()` rejects invalid or colliding codes back to the preset
rather than writing them into records. It accepts enumeration as a whole
sequence, as a positional patch (`{"enum": {"e1": "d"}}`), and still accepts the
old `vol`/`issue`/`part` keys as positions 1–3 so a stored setting keeps working.

## Expected output counts

Measured against the private files above. Any deviation means something broke.

| check | expected |
|---|---|
| converter, well-formed file, standard | 38 853s / 114 863s |
| converter, unkempt file, house | 52 853s / 166 863s |
| `$8` integrity, both files | no duplicate 853 `$8`, no orphaned 863, idempotent across repeated runs |
| parse rate, unkempt | 48 / 51 |
| parse rate, well-formed | 114 / 116 |
| detector, max regex length | 2,000 characters or fewer |

## Layout

```
workbench/            detect -> confirm -> convert, in one app (port 5003)
converter/            866 -> 853/863 converter (port 5000)
  holdings_parser.py    866 text -> structured ranges. TWO grammars:
                        enumeration-first (v.1:no.2(1990)) and a separate
                        chronology-first "block" grammar (1993: (1 [Feb])),
                        dispatched by _looks_like_block()
  marc_converter.py     ranges -> 853/863; convention resolution;
                        convert_record() decides $8 linking across a record
pattern-detector/     866 -> regex clusterer (port 5001)
shared/               ui.css and about.json, served by all three apps
tests/                pytest suite covering all three apps
scripts/              corpus_report.py — reports, asserts nothing
data/                 synthetic .mrc samples + the textual holdings corpus
deploy/               systemd units + nginx config for the hosted demo
ai-regex/             experimental LLM regex generation, not part of the tools
pnx-lookup/           separate local tool
```

The apps duplicate about twenty lines of Jinja — the about-dialog block —
because they have separate `template_folder`s. Content stays single-sourced in
`shared/about.json`. That was a deliberate call over introducing a shared
template path.

## Deployment

A demonstration, not a long-term service: gunicorn behind nginx.

```bash
cd ~/marc-serials-toolkit && git pull && sudo systemctl restart mcsite-converter mcsite-patterns
```

nginx proxies `/converter/` to `127.0.0.1:8001` and `/patterns/` to `:8002`, and
the trailing-slash `proxy_pass` strips the path prefix. **Templates therefore use
relative URLs** (`api/detect`, `ui.css`) — an absolute `/ui.css` would break
behind the proxy. Keep that convention.

## Loose ends

- **`gunicorn` is in neither `requirements.txt`**, though both systemd units
  depend on it. `deploy/README.md` installs it by hand.
- **The record list shows only two or three records at a time** at 1280×900, a
  consequence of accessible sizing (44px targets, 18px body). Deliberate; worth
  revisiting with the cataloguer in front of it.
- **Licensing is unresolved.** MIT was withdrawn pending institutional IP review;
  see `NOTICE.md`. No license is currently granted, and the intent is
  AGPL-3.0-or-later. The git history still contains the old MIT `LICENSE` file —
  deleting it did not remove it from earlier commits, which `NOTICE.md` states
  plainly.
