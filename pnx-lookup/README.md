# PNX Lookup

A small **local** web tool for library staff: enter an MMS ID and get a record's
full **normalized PNX** from an Ex Libris Primo catalog — including internal
fields the public view hides — displayed in clean tables with CSV / Excel export.

It needs **no API key**. Instead it drives a headless browser to load the
record's `showPnx` view and reads the PNX JSON straight out of the page.

## Setup (one time)

```bash
cd pnx-lookup
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

```bash
python pnx_lookup_app.py
# opens http://localhost:8765 automatically
```

Open **Institution settings** and enter your **Primo host** (e.g.
`your-inst.primo.exlibrisgroup.com`) and **view id / vid** (e.g.
`01ABC_INST:01ABC`); optionally adjust search scope and tab. These are saved in
your browser. Then enter an MMS ID and look it up. Use the **Filter** box to
narrow the displayed fields, and **Export CSV / Excel** to save the record.

## Host allowlist

For safety, the lookup only accepts **Ex Libris Primo** hosts (anything ending
in `primo.exlibrisgroup.com`). If your library fronts Primo with a vanity domain
(e.g. `search.lib.example.edu`), add it via an environment variable:

```bash
export PNX_EXTRA_HOSTS="search.lib.example.edu,discovery.example.edu"
```

## Environment variables

| Variable | Purpose |
|---|---|
| `PNX_PORT` | Port to listen on (default `8765`) |
| `PNX_NO_BROWSER` | Set to any value to skip auto-opening a browser |
| `PNX_EXTRA_HOSTS` | Comma-separated extra allowed hosts (vanity Primo domains) |

## Note

This is designed to run **locally on a staff workstation**, one user at a time —
it keeps a single browser page open and serves on `127.0.0.1`. It is not
intended as a public, multi-user web service (a headless browser per request is
heavy, and the allowlist is the only abuse control).
