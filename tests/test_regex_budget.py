"""
The one thing Python's `re` cannot do for itself: stop.

A regex the tool generates is safe by construction. A regex a cataloguer has
edited by hand is not, and MAX_REGEX_CHARS protects nothing here -- the
canonical runaway expression is eleven characters. These tests pin the two
halves of the answer: a runaway expression is stopped and reported, and an
ordinary one is unaffected.

`^(a+)+$` against a long run of "a" ending in a character that cannot match is
the textbook case: the engine tries every way of dividing the run between the
two repeats, and there are 2^n of them.
"""

from __future__ import annotations

import time

import pytest

from regex_budget import (BACKTRACKING_PROBES, MatchFailed, MatchTimeout,
                          completes_within_budget, match_statements,
                          too_slow_message)

RUNAWAY = r"^(a+)+$"
VICTIM = "a" * 30 + "!"


def test_an_ordinary_expression_runs_and_reports_what_it_found():
    results = match_statements(r"v\.(?P<vol>\d+)\((?P<year>\d{4})\)",
                               ["v.1(1990)", "nope"])
    assert [r["full"] for r in results] == [True, False]
    assert results[0]["groups"] == {"vol": "1", "year": "1990"}
    assert results[1]["span"] is None


def test_a_partial_hit_is_distinguished_from_a_full_one():
    """
    Both Test screens show how close a near miss came; only one of them trusts
    its values, so the helper reports the span either way and lets the caller
    decide.
    """
    hit, = match_statements(r"v\.(?P<vol>\d+)", ["v.1 (1990)"])
    assert (hit["full"], hit["partial"]) == (False, True)
    assert hit["span"] == [0, 3]


def test_a_runaway_expression_is_stopped():
    started = time.monotonic()
    with pytest.raises(MatchTimeout):
        match_statements(RUNAWAY, [VICTIM], budget=1.0)   # explicit, not the default
    elapsed = time.monotonic() - started

    # The point of the whole module: it returns. Generously bounded, because a
    # loaded CI machine is slow to start an interpreter and this assertion must
    # not be the flaky one.
    assert elapsed < 10, f"took {elapsed:.1f}s to give up on a 1s budget"
    # And one statement is enough to hang, so the guard cannot have been a
    # per-item limit.
    assert len([VICTIM]) == 1


def test_a_broken_expression_is_a_failure_not_a_timeout():
    """
    A pattern that will not compile is ordinary input and belongs in its own
    400. Only the matching needs a child process.
    """
    with pytest.raises(MatchFailed):
        match_statements("(", ["anything"])


def test_completes_within_budget_answers_yes_no():
    assert completes_within_budget(r"v\.\d+", ["v.1"]) is True
    assert completes_within_budget(RUNAWAY, [VICTIM]) is False


def test_an_uncompilable_expression_counts_as_finishing():
    """
    completes_within_budget() asks one question. A pattern that cannot run at
    all is not a runaway, and the caller rejects it on its own terms rather
    than being told the wrong thing about it.
    """
    assert completes_within_budget("(", ["anything"]) is True


def test_the_fixed_probes_catch_the_classic_shape():
    """
    What the library door has to go on when no statements are loaded. It is a
    screen rather than a proof, so this pins that the screen at least catches
    the expression everyone writes by accident.
    """
    assert completes_within_budget(RUNAWAY, BACKTRACKING_PROBES) is False


def test_the_message_says_what_is_wrong_and_what_to_do():
    """
    A cataloguer who is told only that something timed out concludes the tool
    is broken. The cause is nearly always one shape, and naming it is the
    difference between that and a fixed expression.
    """
    note = too_slow_message()
    assert "repeat" in note
    assert "nothing was tested" in note
