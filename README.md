# MARC Serials Toolkit

A small toolkit for **enhancing MARC serials holdings** — turning the free-text
holdings summaries libraries keep in the MARC **866** field into structured,
machine-actionable **853 / 863** enumeration-and-chronology fields, and for
exploring different ways (including AI) of doing so at scale.

It grew out of a real cataloging problem and is the subject of an upcoming
conference presentation on applying AI to serials-holdings enhancement.

## The tools

Each tool can be installed and run on its own. All but the Workbench are
self-contained; the Workbench is the other two joined up, and imports their
engines rather than copying them, so it needs the whole repository present.

| Tool | Folder | What it does | Type |
|---|---|---|---|
| **Holdings Workbench** | [`workbench/`](workbench/) | Detect patterns, confirm what each captured value means in MARC, and convert with them — the other two tools joined up | Flask web app |
| **Converter** | [`converter/`](converter/) | Convert an 866 statement — or a whole MARC file — into structured 853 / 863 fields | Flask web app |
| **Pattern Detector** | [`pattern-detector/`](pattern-detector/) | Scan a collection of 866 statements, cluster them by structure, and generate a named-group regex per pattern | Flask web app |
| **PNX Lookup** | [`pnx-lookup/`](pnx-lookup/) | Look up a record's full normalized PNX from an Ex Libris Primo catalog by MMS ID — no API key — with a table view and CSV/Excel export | Local web app (headless browser) |
| **AI Regex Generator** | [`ai-regex/`](ai-regex/) | Use an LLM to generate a parsing regex from sample holdings (an exploratory approach) | CLI / experimental |

The Workbench, Converter and Pattern Detector are deterministic — no network
calls, no API key. The AI Regex Generator calls the OpenAI API and requires your
own key (see [`ai-regex/README.md`](ai-regex/README.md)).

The Workbench does not replace the other two, and does not copy them: it imports
their engines, so a fix to the parser or the detector reaches all three. Use the
Converter or the Pattern Detector on its own when that is all you need; use the
Workbench when the detector has found a pattern the converter should be using.

## Quick start

Pick a tool, install just its requirements in a virtual environment, and run it.

**Holdings Workbench** (opens at http://localhost:5003). Unlike the other two it
is not self-contained: it imports the Converter's and the Pattern Detector's
engine modules, so it needs the whole repository present. It finds them relative
to its own file, so it can be started from any directory.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r workbench/requirements.txt
python workbench/app.py
```

**Converter** (opens at http://localhost:5000):

```bash
cd converter
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

**Pattern Detector** (opens at http://localhost:5001):

```bash
cd pattern-detector
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

**PNX Lookup** — see [`pnx-lookup/README.md`](pnx-lookup/README.md); it needs
Playwright and a headless browser, and runs locally.

**AI Regex Generator** — see [`ai-regex/README.md`](ai-regex/README.md); it needs
an `OPENAI_API_KEY`.

## Repository layout

```
marc-serials-toolkit/
├── workbench/          detect → confirm → convert, in one app (Flask web app)
├── converter/          866 → 853/863 converter (Flask web app)
├── pattern-detector/   866 pattern detector + regex generator (Flask web app)
├── pnx-lookup/         Primo PNX record lookup (local web app; needs Playwright)
├── ai-regex/           LLM-based regex generation (CLI/experimental)
├── tests/              pytest suite covering all three apps
├── data/
│   ├── example_holdings.mrc   Small SYNTHETIC sample for demos/tests
│   ├── messy_holdings.mrc     SYNTHETIC awkward cases, for the test suite
│   └── textual_holdings_corpus.txt  112 real 866 $a statements (text, not MARC)
├── scripts/
│   ├── create_example_mrc.py  Regenerates the synthetic sample
│   ├── create_messy_mrc.py    Regenerates the awkward-case fixture
│   └── corpus_report.py       Runs the corpus through all three engines
├── CORPUS-FINDINGS.md  What that corpus reveals about the three tools
├── NOTICE.md           Licensing status — no license currently granted
├── THIRD-PARTY-NOTICES.md  Attribution for derived third-party code
└── .gitignore
```

## Sample data

Both `.mrc` files in `data/` are **invented** records — no real institutional
data. `example_holdings.mrc` is the demo sample; `messy_holdings.mrc` collects
the awkward shapes the test suite needs (the chronology-first grammar, slash-
separated ranges, records that already carry an 853). Regenerate either with:

```bash
pip install pymarc
python scripts/create_example_mrc.py
python scripts/create_messy_mrc.py
```

`data/textual_holdings_corpus.txt` is different in kind: 112 unique 866 `$a`
statements transcribed from real catalogue records, as plain text rather than
MARC. It covers far more caption and chronology styles than the synthetic
fixtures do, and it exists to find where the engines fall short. It carries no
patron data, no local identifiers and no institutional codes — only enumeration
and chronology strings. Run it through all three engines with:

```bash
python scripts/corpus_report.py            # summary
python scripts/corpus_report.py --detail   # every affected statement
python scripts/corpus_report.py --drift    # only outcomes that have changed
```

[`CORPUS-FINDINGS.md`](CORPUS-FINDINGS.md) records what it revealed and what has
been fixed since. Five findings are fixed so far, taking silent losses from 32%
of statements to 13%; what remains is 71% converting cleanly, 13% still losing a
value without saying so, and a detector that splits four cataloguer-visible
shapes across fifteen patterns.

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite runs in under a second: everything is in-process, with no servers and
no network. A few tests are marked `xfail` — they describe known defects and say
what the behaviour should be, so fixing one turns its test green.

The exact output counts recorded in `HANDOFF.md` were measured against private
holdings files that are deliberately not in this repository. They are skipped
unless you point the suite at them:

```bash
MARC_TEST_DATA_DIR=/path/to/mounted/share python -m pytest -m calibration
```

## How the Workbench joins the two tools

The Pattern Detector generates a regex whose capture groups are named for the
level and boundary they hold — `start_vol`, `end_year`. What it cannot know is
whether that reading is *right*: which number is a volume rather than an issue,
and whether a value belongs to the holdings at all. The Workbench asks, once per
pattern, and then converts every matching statement with the answer.

That confirmation step is not ceremony. A bare number — the `16` in `?: 16`, or
the `5` in `v.1(1990)-5(1994)` where no caption reaches across the separator —
carries nothing at all to say which level it belongs to. That is why the
Converter refuses to guess and holds such statements for review, and no amount
of better parsing can fix it: the information is not in the statement. A
cataloguer who knows the collection can supply it in a moment, once.

Statements no confirmed pattern matches are parsed by
`holdings_parser.parse_866()` exactly as the Converter parses them, so an empty
pattern library produces output identical to the Converter's — asserted byte for
byte in `tests/test_workbench_api.py`.

## Notes on the MARC fields

The **866** field holds a human-readable "textual holdings" summary such as
`v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)`. The **853** (captions & pattern) and
**863** (enumeration & chronology) fields encode the same information in a
structured, parseable form. Converting 866 → 853/863 across messy real-world
data — with dozens of caption styles — is what these tools are for. See
[`converter/`](converter/) for the full field-by-field breakdown.

## License

**No license is granted at this time.** This repository is published for
reference only while institutional intellectual property rights are under
review; default copyright applies. The intent is to release under
`AGPL-3.0-or-later` once that review concludes. See [NOTICE.md](NOTICE.md) for
the full statement, including the status of the MIT license carried by earlier
commits.

Portions of this project derive from third-party code — see
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
