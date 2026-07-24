# MARC Serials Toolkit

A small toolkit for **enhancing MARC serials holdings** — turning the free-text
holdings summaries libraries keep in the MARC **866** field into structured,
machine-actionable **853 / 863** enumeration-and-chronology fields, and for
exploring different ways (including AI) of doing so at scale.

It grew out of a real cataloging problem and is the subject of an upcoming
conference presentation on applying AI to serials-holdings enhancement.

## The tools

Each tool is self-contained and can be installed and run on its own — you do
**not** need the others (or an API key) to use any single one.

| Tool | Folder | What it does | Type |
|---|---|---|---|
| **Converter** | [`converter/`](converter/) | Convert an 866 statement — or a whole MARC file — into structured 853 / 863 fields | Flask web app |
| **Pattern Detector** | [`pattern-detector/`](pattern-detector/) | Scan a collection of 866 statements, cluster them by structure, and generate a named-group regex per pattern | Flask web app |
| **PNX Lookup** | [`pnx-lookup/`](pnx-lookup/) | Look up a record's full normalized PNX from an Ex Libris Primo catalog by MMS ID — no API key — with a table view and CSV/Excel export | Local web app (headless browser) |
| **AI Regex Generator** | [`ai-regex/`](ai-regex/) | Use an LLM to generate a parsing regex from sample holdings (an exploratory approach) | CLI / experimental |

The Converter and Pattern Detector are deterministic — no network calls, no
API key. The AI Regex Generator calls the OpenAI API and requires your own key
(see [`ai-regex/README.md`](ai-regex/README.md)).

## Quick start

Pick a tool, install just its requirements in a virtual environment, and run it.

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
├── converter/          866 → 853/863 converter (Flask web app)
├── pattern-detector/   866 pattern detector + regex generator (Flask web app)
├── pnx-lookup/         Primo PNX record lookup (local web app; needs Playwright)
├── ai-regex/           LLM-based regex generation (CLI/experimental)
├── data/
│   └── example_holdings.mrc   Small SYNTHETIC sample for demos/tests
├── scripts/
│   └── create_example_mrc.py  Regenerates the synthetic sample
├── LICENSE             MIT
└── .gitignore
```

## Sample data

`data/example_holdings.mrc` is a small file of **invented** records — no real
institutional data. Regenerate or extend it with:

```bash
pip install pymarc
python scripts/create_example_mrc.py
```

## Notes on the MARC fields

The **866** field holds a human-readable "textual holdings" summary such as
`v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)`. The **853** (captions & pattern) and
**863** (enumeration & chronology) fields encode the same information in a
structured, parseable form. Converting 866 → 853/863 across messy real-world
data — with dozens of caption styles — is what these tools are for. See
[`converter/`](converter/) for the full field-by-field breakdown.

## License

MIT — see [LICENSE](LICENSE).
