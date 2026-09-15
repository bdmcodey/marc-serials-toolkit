"""
MARC Serials Toolkit — the holdings pipeline.

One installable package holding every engine the tools run on, so that a fix
to the parser reaches the Converter, the Pattern Detector and the Workbench
without any of them importing the others by path.

    parser     866 text            -> ParseResult (ranges, levels, chronology)
    converter  ParseResult         -> MARC 853 / 863 fields
    detector   many 866 statements -> clusters, each with a named-group regex
    bridge     a confirmed pattern -> the same ParseResult the parser produces
    library    the confirmed patterns a cataloguer has built up
    budget     runs a regex in a child process, under a wall-clock limit

The web applications in converter/, pattern-detector/ and workbench/ are
adapters over this package. They hold routes, templates and session handling,
and no holdings logic of their own.
"""

__all__ = ["parser", "converter", "detector", "bridge", "library", "budget"]
