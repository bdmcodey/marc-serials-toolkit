"""
MARC Serials Toolkit — the holdings pipeline.

One installable package: every engine, and the application that serves them.

    parser     866 text            -> ParseResult (ranges, levels, chronology)
    converter  ParseResult         -> MARC 853 / 863 fields
    detector   many 866 statements -> clusters, each with a named-group regex
    bridge     a confirmed pattern -> the same ParseResult the parser produces
    library    the confirmed patterns a cataloguer has built up
    budget     runs a regex in a child process, under a wall-clock limit
    records    reading MARC records and writing a conversion onto one
    store      the per-session file store, and the sweep that ages it out
    webapp     the Flask application: routes, templates and session handling,
               and no holdings logic of its own

It was three applications on three ports until September 2026 -- a converter, a
pattern detector, and a workbench that joined them up.
"""

__all__ = ["parser", "converter", "detector", "bridge", "library",
           "budget", "records", "store", "webapp"]
