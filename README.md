# MARC Serials Toolkit

A small toolkit for **enhancing MARC serials holdings** — turning the free-text
holdings summaries libraries keep in the MARC **866** field into structured,
machine-actionable **853 / 863** enumeration-and-chronology fields.

It grew out of a real cataloging problem and is the subject of an upcoming
conference presentation on applying AI to serials-holdings enhancement.

## What it does

One application, run locally on your own machine. It is deterministic — no
network calls, no API key, nothing leaves the machine.

| Step | What happens |
|---|---|
| **Holdings** | Upload a MARC file, or paste 866 statements. One statement can be converted on its own, with no file and no pattern. |
| **Patterns** | The 866 statements are clustered by structure and a named-group regex is generated for each cluster. You confirm what each captured value means in MARC — which number is a volume, which is an issue, what caption the 853 should declare — once per pattern. |
| **Convert** | Every record is converted, with your confirmed patterns supplying what the parser cannot work out on its own. Review the file record by record and download it. |

It was three separate web apps until September 2026 — a Converter, a Pattern
Detector, and a Workbench that joined them up. They shared a goal and duplicated
each other's code, and the copies drifted far enough that one of them deleted
the other's pattern libraries. Everything the three did is in the one
application; nothing was dropped.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
marc-serials
```

Then open <http://localhost:5003>. Port 5003 rather than 5000 because macOS
gives 5000 to AirPlay Receiver; set `MARC_PORT` to change it.

From a clone, without installing:

```bash
pip install flask pymarc
python run.py
```

## Repository layout

```
marc-serials-toolkit/
├── marc_serials/       the whole tool, as one installable package
│   ├── webapp.py           the Flask application: routes and session handling
│   ├── templates/          the page
│   ├── shared/             stylesheet and version/changelog
│   ├── parser.py           866 text            → ParseResult
│   ├── converter.py        ParseResult         → MARC 853 / 863 fields
│   ├── detector.py         many 866 statements → clusters, each with a regex
│   ├── bridge.py           a confirmed pattern → the parser's ParseResult
│   ├── library.py          the patterns a cataloguer has confirmed
│   ├── records.py          reading and writing MARC records
│   ├── store.py            the per-session file store, and its sweep
│   └── budget.py           runs a regex in a child process, under a time limit
├── run.py              start it without installing
├── pyproject.toml      package metadata and the runtime pins
├── tests/              pytest suite
├── data/
│   ├── example_holdings.mrc   Small SYNTHETIC sample for demos/tests
│   ├── messy_holdings.mrc     SYNTHETIC awkward cases, for the test suite
│   └── textual_holdings_corpus.txt  117 real 866 $a statements (text, not MARC)
├── scripts/
│   ├── create_example_mrc.py  Regenerates the synthetic sample
│   ├── create_messy_mrc.py    Regenerates the awkward-case fixture
│   └── corpus_report.py       Runs the corpus through the engines
├── CORPUS-FINDINGS.md  What that corpus reveals about the tool
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

`data/textual_holdings_corpus.txt` is different in kind: 117 unique 866 `$a`
statements transcribed from real catalogue records, as plain text rather than
MARC. It covers far more caption and chronology styles than the synthetic
fixtures do, and it exists to find where the engines fall short. It carries no
patron data, no local identifiers and no institutional codes — only enumeration
and chronology strings. Run it through the engines with:

```bash
python scripts/corpus_report.py            # summary
python scripts/corpus_report.py --detail   # every affected statement
python scripts/corpus_report.py --drift    # only outcomes that have changed
```

[`CORPUS-FINDINGS.md`](CORPUS-FINDINGS.md) records what it revealed and what has
been fixed since. Silent losses — a value dropped with nothing on screen to say
so — are down from 32% of statements to **none**: 77% now convert cleanly, 19%
convert while naming a value they could not place, and 4% are held for review
rather than half-converted. On the detector side, no shape is split across
clusters any more.

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite runs in under a second: everything is in-process, with no servers and
no network. A few tests are marked `xfail` — they describe known defects and say
what the behaviour should be, so fixing one turns its test green.

A further set of tests pins exact output counts against real library holdings
that are deliberately not in this repository — the counts live in
`tests/test_calibration.py`. They skip unless you point the suite at the
directory holding those files:

```bash
MARC_TEST_DATA_DIR=/path/to/holdings python -m pytest -m calibration
```

## Why confirming a pattern is not ceremony

Pattern detection generates a regex whose capture groups are named for the
level and boundary they hold — `start_vol`, `end_year`. What it cannot know is
whether that reading is *right*: which number is a volume rather than an issue,
and whether a value belongs to the holdings at all. The tool asks, once per
pattern, and then converts every matching statement with the answer.

That confirmation step is not ceremony. A bare number — the `16` in `?: 16`, or
the `5` in `v.1(1990)-5(1994)` where no caption reaches across the separator —
carries nothing at all to say which level it belongs to. That is why the parser
refuses to guess and holds such statements for review, and no amount of better
parsing can fix it: the information is not in the statement. A
cataloguer who knows the collection can supply it in a moment, once.

For enumeration the screen asks two further things, both with a default it
suggests rather than imposes: the **caption** — the word the 853 will declare,
offered from the familiar list but editable to whatever the statement uses — and
the **level** it sits at, which defaults to the order the values appear in.
Enumeration is an ordered list of any depth, not volume-then-issue-then-part:
MARC 21 puts captions in `$a`–`$f` "in descending order of significance" and says
nothing about which words go in them, so a title numbered by issue alone quite
properly gets `$a no.`

Every statement is read by `marc_serials.parser.parse_866()`, whether a pattern
matches it or not, so an empty pattern library converts exactly as the plain
parser does — asserted against the engine itself in `tests/test_api_patterns.py`.
A confirmed pattern supplies what the parser cannot work out on its own: the
caption for a level written as a bare number, and the meaning of a value nothing
in the statement can type.

Until 0.10.0 a confirmed pattern read a statement a second way, from its own
capture groups, and the two readings could disagree. Measured across all 141
statements in the corpus and the `.mrc` fixtures, they agreed on 90 and
disagreed on 10 — and on nine of those ten the pattern was the wrong one. There
is now one reader, and `tests/test_invariants.py` asserts that both paths write
the same 863.

## Notes on the MARC fields

The **866** field holds a human-readable "textual holdings" summary such as
`v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)`. The **853** (captions & pattern) and
**863** (enumeration & chronology) fields encode the same information in a
structured, parseable form. Converting 866 → 853/863 across messy real-world
data — with dozens of caption styles — is what this tool is for. See
[`marc_serials/converter.py`](marc_serials/converter.py) for the full
field-by-field breakdown.

## License

**No license is granted at this time.** This repository is published for
reference only while institutional intellectual property rights are under
review; default copyright applies. See [NOTICE.md](NOTICE.md) for the full
statement.

Portions of this project derive from third-party code — see
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
