"""
Run a user-supplied regular expression under a wall-clock budget.

A regex the *tool* generates is safe: `_build_regex()` emits an alternation of
literal and character-class parts with no nested quantifier, and the worst one
the corpus produces searches an adversarial 500-character string in well under a
millisecond.  A regex a *cataloguer* has edited by hand is a different thing.
`^(\\s*\\w+)*$` is eleven characters and will not finish this decade on a string
of thirty spaces, which is why MAX_REGEX_CHARS protects nothing here -- length
is nearly uncorrelated with catastrophic backtracking, and the comment on that
constant says so.

Python's `re` cannot be interrupted: no timeout argument, and a match in
progress ignores signals until it returns.  So the match runs in a **child
process** and the parent kills it when the budget runs out.  The alternatives
were considered and rejected:

* `signal.setitimer` only works in the main thread.  Gunicorn's sync workers
  would be fine, but Flask's development server is threaded by default and the
  guard would silently do nothing there -- a safety measure that is absent in
  some configurations and present in others is worse than an honest one.
* The third-party `regex` module takes a `timeout=`, and adding a dependency to
  a tool a librarian installs with `pip install flask pymarc gunicorn` costs
  more than this file does.

What this bounds, and what it does not.  A request cannot wedge a worker: the
endpoint returns, the child is killed, and the worker is immediately reusable.
It is not a proof that an expression is safe -- an expression that completes on
these statements may still blow up on a statement nobody has tried yet.  The
claim is only "this finished, on this input, within this long".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Optional, Sequence

# Measured on the 117-statement corpus, against the longest expression the
# detector generates (2,384 characters) searching 2,000 copies of a 500-character
# adversarial string -- the largest payload any endpoint here will accept:
#
#     matching alone                         26 ms
#     the whole round trip, child included  321 ms
#     an empty round trip (interpreter start, one statement)   37 ms
#
# So the budget has to cover the child's startup and the JSON in both
# directions, not just the matching -- and a killed child cannot report how it
# spent its time, which is why the budget is wall clock from the parent.  Five
# seconds is about fifteen times that worst case.  The headroom is deliberate
# and the trade is asymmetric: three more seconds of waiting costs a cataloguer
# very little, and an expression wrongly refused for being slow costs them a
# pattern that was fine.
#
# MARC_MATCH_BUDGET overrides it.  Read once at import, and again *per call*
# below, so shortening it reaches every caller rather than only those that had
# not already bound it as a default argument.
try:
    MATCH_BUDGET_SECONDS = float(os.environ.get("MARC_MATCH_BUDGET", "") or 5.0)
except ValueError:                               # pragma: no cover - typo in env
    MATCH_BUDGET_SECONDS = 5.0

# What the worker is handed.  Bounded here as well as at each endpoint, because
# this module is the thing that promises to return.
MAX_STATEMENTS = 2000
MAX_STATEMENT_CHARS = 500


# Strings that provoke the classic runaway shapes, for the doors where there is
# no particular text to test against -- storing a pattern in the library, say.
# Each is a long run of one kind of character with a final character that makes
# the match fail, which is what turns a repeat inside a repeat into an
# exhaustive search.
#
# This is a screen, not a proof. An expression that survives these may still
# blow up on a statement nobody has tried, and the only honest guarantee remains
# the one match_statements() gives: whatever runs, stops.
BACKTRACKING_PROBES = (
    "a" * 40 + "!",
    "1" * 40 + "!",
    " ".join(["ab"] * 24) + "!",
    "v. " + "1" * 30 + " no. " + "2" * 30 + "!",
    "(" + "1990-" * 12 + "!",
)


class MatchTimeout(Exception):
    """The expression did not finish within its budget and was stopped."""


class MatchFailed(Exception):
    """The child could not run the expression at all -- see the message."""


def _worker(payload: dict) -> dict:
    """The matching itself, and the only thing that runs in the child."""
    import re

    compiled = re.compile(payload["regex"], re.IGNORECASE)
    results = []
    for raw in payload["statements"]:
        s = (raw or "").strip()
        full = compiled.fullmatch(s)
        partial = None if full else compiled.search(s)
        m = full or partial
        results.append({
            "full": full is not None,
            "partial": partial is not None,
            "groups": m.groupdict() if m else {},
            "span": list(m.span()) if m else None,
        })
    return {"ok": True, "results": results}


def match_statements(regex: str, statements: Sequence[str],
                     budget: Optional[float] = None) -> list:
    """
    Match `regex` against each statement, or raise MatchTimeout.

    One result per statement, in order:

        {"full": bool, "partial": bool, "groups": {...}, "span": [start, end]}

    `groups` comes from the full match where there is one and the partial hit
    otherwise, so a caller that only trusts a full match clears them itself --
    both Test screens want the span of a near miss, and only one of them wants
    its values.

    Compile the expression in the caller first.  A broken pattern is ordinary
    input and deserves its own 400, and compiling is bounded work; it is only
    the *matching* that needs a child process.
    """
    budget = MATCH_BUDGET_SECONDS if budget is None else budget
    payload = {
        "regex": regex,
        "statements": [str(s)[:MAX_STATEMENT_CHARS]
                       for s in list(statements)[:MAX_STATEMENTS]],
    }

    try:
        done = subprocess.run(
            [sys.executable, os.path.abspath(__file__)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=budget,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run has already killed it and reaped it.
        raise MatchTimeout(
            f"The expression did not finish within {budget:g} seconds and was "
            f"stopped."
        ) from None
    except OSError as exc:                       # pragma: no cover - no child
        raise MatchFailed(f"Could not start the matcher: {exc}") from None

    if done.returncode != 0:
        raise MatchFailed(done.stderr.strip().splitlines()[-1]
                          if done.stderr.strip() else
                          f"The matcher exited with status {done.returncode}.")

    try:
        body = json.loads(done.stdout)
    except ValueError:
        raise MatchFailed("The matcher returned nothing readable.") from None

    if not body.get("ok"):
        raise MatchFailed(body.get("error") or "The expression could not be run.")
    return body["results"]


def too_slow_message(budget: Optional[float] = None) -> str:
    """
    What to say when an expression is stopped, and what to do about it.

    The cause is almost always one shape: a repeat inside a repeat, such as
    "(\\d+)+" or "(\\s*\\w+)*".  On a statement that nearly matches, the engine
    tries every way of dividing the text between the two repeats, and there are
    more of those than there are seconds.  Naming it is the difference between a
    cataloguer fixing the expression and a cataloguer concluding the tool is
    broken.
    """
    budget = MATCH_BUDGET_SECONDS if budget is None else budget
    return (
        f"This expression did not finish within {budget:g} seconds and was "
        f"stopped, so nothing was tested. That almost always means one repeat "
        f"is inside another -- something like (\\d+)+ or (\\s*\\w+)* -- which "
        f"the matcher can take effectively forever on a statement that nearly "
        f"matches. Rewriting the inner part without its own repeat normally "
        f"fixes it."
    )


def completes_within_budget(regex: str, statements: Sequence[str],
                            budget: Optional[float] = None) -> bool:
    """
    Whether `regex` finishes against `statements` inside the budget.

    For the doors where the result is not wanted, only the assurance: storing a
    pattern in the library, or previewing a candidate through the whole
    conversion.  Both would otherwise run the expression against every statement
    of every record with nothing able to stop it.

    A pattern that cannot even be compiled is not this function's business and
    counts as finishing; the caller rejects it on its own terms.
    """
    try:
        match_statements(regex, statements, budget)
    except MatchTimeout:
        return False
    except MatchFailed:
        return True
    return True


def main() -> int:                               # pragma: no cover - the child
    """Read one JSON request from stdin, write one JSON response to stdout."""
    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        json.dump({"ok": False, "error": f"unreadable request: {exc}"}, sys.stdout)
        return 0

    try:
        json.dump(_worker(payload), sys.stdout)
    except Exception as exc:                     # re.error, MemoryError, ...
        json.dump({"ok": False, "error": str(exc)}, sys.stdout)
    return 0


if __name__ == "__main__":                       # pragma: no cover - the child
    sys.exit(main())
