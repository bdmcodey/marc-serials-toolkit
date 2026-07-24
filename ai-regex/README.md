# AI Regex Generator (experimental)

An exploratory, **command-line** approach to serials-holdings enhancement: use
an LLM to generate a Python regex that parses a collection's 866 holdings
statements, then validate that regex against real samples.

Unlike the Converter and Pattern Detector, this is **not** a web app and it
**requires your own OpenAI API key**. It's included here as part of the
"different ways to approach the problem" exploration behind the project's
conference presentation.

## Setup

```bash
cd ai-regex
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# then edit .env and paste in your own OPENAI_API_KEY
```

> **Never commit `.env`.** It is gitignored. Only `.env.example` (a placeholder)
> belongs in version control.

## Pieces

| File | Role |
|---|---|
| `ai_regex_generator.py` | Builds the prompt, calls OpenAI, extracts a regex from the response |
| `sample_collector.py` | Pulls 866 sample statements out of a MARC file |
| `regex_validator.py` | Scores a candidate regex against a batch of statements |
| `pattern_manager.py` | Saves/loads named patterns and default regexes |
| `test_enum_update.py` | Desktop harness that ties the above together (uses `tkinter` for a file picker, so it needs a graphical environment) |

## Cost & safety notes

- Each generation makes a paid OpenAI API call. Keep an eye on usage.
- `test_enum_update.py` opens a native file dialog via `tkinter`; run it on a
  desktop, not a headless server.
- The generated regex is compiled and tested locally before use.
