"""
Tests for holdings_parser.parse_866().

The parser carries two grammars behind one entry point. parse_866() asks
_looks_like_block() whether a statement is chronology-first ("1993: (1 [Feb])")
and dispatches to a separate block parser if so; everything else goes through
the enumeration-first path ("v.1:no.2(1990)"). A third path, _parse_degenerate(),
catches single-value statements neither grammar accepts.

Most assertions here are characterization: they record what the parser does
today so that a refactor has to be deliberate about changing it. Where current
behaviour is a defect rather than a decision, the test is marked xfail and says
what it should do instead.
"""

from __future__ import annotations

import pytest

from holdings_parser import (parse_866, _looks_like_block, chron_unit_code,
                             normalise_chron_unit)


# ---------------------------------------------------------------------------
# Enumeration-first grammar
# ---------------------------------------------------------------------------

def test_full_range_with_enumeration_and_chronology():
    r = parse_866("v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)")
    assert r.success is True
    assert len(r.ranges) == 1

    start, end = r.ranges[0].start, r.ranges[0].end
    assert (start.vol, start.issue, start.year, start.month) == ("1", "1", "1990", "01")
    assert (end.vol, end.issue, end.year, end.month) == ("5", "4", "1994", "12")
    assert r.ranges[0].open_ended is False


def test_caption_variants_and_seasons_parse_identically():
    """
    "Vol. 1, No. 1 (Spring 1990)" must reach the same structure as the terse
    form. Seasons become MARC season codes (21 Spring .. 24 Winter), not months.
    """
    r = parse_866("Vol. 1, No. 1 (Spring 1990)-Vol. 5, No. 4 (Winter 1994)")
    assert len(r.ranges) == 1

    start, end = r.ranges[0].start, r.ranges[0].end
    assert (start.vol, start.issue, start.year, start.month) == ("1", "1", "1990", "21")
    assert (end.vol, end.issue, end.year, end.month) == ("5", "4", "1994", "24")


def test_open_ended_range_has_no_end():
    """A trailing hyphen means "still being received", not a missing endpoint."""
    r = parse_866("v.6(1995)-")
    assert r.ranges[0].open_ended is True
    assert r.ranges[0].end is None
    assert r.ranges[0].start.vol == "6"


def test_compressed_range_keeps_both_endpoints_in_the_start_unit():
    """
    "v. 1-14 (1953-1966)" is a single unit whose values happen to be ranges, not
    two units either side of a separator: the hyphens sit inside the volume and
    the year rather than between two halves. The compressed values are carried
    through to the 863 verbatim, so this shape must not be "helpfully" split.
    """
    r = parse_866("v. 1-14 (1953-1966)")
    assert len(r.ranges) == 1
    assert r.ranges[0].start.vol == "1-14"
    assert r.ranges[0].start.year == "1953-1966"
    assert r.ranges[0].end is None
    assert r.ranges[0].open_ended is False


def test_multi_range_statement_splits_on_comma():
    r = parse_866("v.1(1990)-v.3(1992), v.5(1994)-")
    assert len(r.ranges) == 2
    assert r.ranges[0].start.vol == "1"
    assert r.ranges[0].end.vol == "3"
    assert r.ranges[1].start.vol == "5"
    assert r.ranges[1].open_ended is True


def test_year_only_range():
    r = parse_866("1990-1994")
    assert len(r.ranges) == 1
    assert r.ranges[0].start.year == "1990"
    assert r.ranges[0].end.year == "1994"
    assert r.ranges[0].start.vol is None


@pytest.mark.parametrize("text", ["", "   ", "\t\n"])
def test_empty_input_fails_with_a_warning(text):
    r = parse_866(text)
    assert r.success is False
    assert r.ranges == []
    assert r.warnings


# ---------------------------------------------------------------------------
# Chronology-first "block" grammar
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, is_block", [
    ("1993: (1 [Feb])", True),
    ("1949: 1 (1-6 [Apr-Sep])", True),
    ("?: 16", True),
    ("N 1993: (1 [Feb])", True),
    ("v.1(1990)", False),
    ("1990-1994", False),
    ("v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)", False),
])
def test_block_dispatch_gate(text, is_block):
    """
    _looks_like_block() decides which grammar runs. If it drifts, statements
    silently change parser without any other symptom, so the gate is pinned
    explicitly rather than only through its downstream effects.
    """
    assert _looks_like_block(text) is is_block


def test_block_number_inside_parens_is_an_issue():
    """Positional role rule: inside the parens, a bare number is the issue."""
    r = parse_866("1993: (1 [Feb])")
    assert len(r.ranges) == 1
    start = r.ranges[0].start
    assert (start.vol, start.issue, start.year, start.month) == (None, "1", "1993", "02")


def test_block_number_before_parens_is_a_volume():
    """The complementary rule: outside the parens, the number is the volume."""
    r = parse_866("1949: 1 (1-6 [Apr-Sep])")
    start, end = r.ranges[0].start, r.ranges[0].end
    assert (start.vol, start.issue, start.year) == ("1", "1-6", "1949")
    assert start.month == "04"
    assert end.month == "09"


def test_multi_year_block_run_on_yields_one_range_per_year():
    r = parse_866("2019: (1-6 [Feb-Nov])2020: (7-12 [Jan-Dec])")
    assert len(r.ranges) == 2
    assert [hr.start.year for hr in r.ranges] == ["2019", "2020"]
    assert r.ranges[0].start.month == "02"
    assert r.ranges[0].end.month == "11"
    assert r.ranges[1].start.issue == "7-12"


def test_unexplained_marker_parses_and_warns():
    """
    A marker the parser does not understand must not cost the statement: parse
    around it and say so, rather than discarding real holdings.
    """
    r = parse_866("N 1994: (2 [Mar])")
    assert r.success is True
    assert len(r.ranges) == 1
    assert r.ranges[0].start.year == "1994"
    assert any("marker" in w.lower() for w in r.warnings)


# ---------------------------------------------------------------------------
# Degenerate statements
# ---------------------------------------------------------------------------

def test_uncertain_year_is_usable_holdings():
    """
    "2016?" is a year the cataloguer was unsure of. It is still holdings data,
    so it parses successfully and the lost qualifier is reported as a warning.
    """
    r = parse_866("2016?")
    assert r.success is True
    assert r.needs_review is False
    assert r.ranges[0].start.year == "2016"
    assert any("uncertain" in w.lower() for w in r.warnings)


@pytest.mark.parametrize("text, missing", [
    ("? 106", "volume, an issue or a year"),
    ("?: 16", "volume or an issue"),
])
def test_bare_number_is_held_for_review(text, missing):
    """
    A number with nothing to say what it counts cannot be encoded safely. It is
    held for review rather than guessed at -- and the two grammars word the
    warning differently, which is asserted so they cannot be quietly merged.
    """
    r = parse_866(text)
    assert r.success is False
    assert r.needs_review is True
    assert r.ranges == []
    assert missing in r.warnings[0]


def test_needs_review_is_distinct_from_failure():
    """
    success and needs_review are independent flags, and both convert_holdings()
    and the batch API branch on them separately. A hard parse failure leaves
    needs_review False; only a statement whose values were found but could not
    be placed sets it.
    """
    unplaceable = parse_866("? 106")
    assert (unplaceable.success, unplaceable.needs_review) == (False, True)

    unreadable = parse_866("see note")
    assert (unreadable.success, unreadable.needs_review) == (False, False)


# ---------------------------------------------------------------------------
# Chronology helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, code", [
    ("Jan.", "01"), ("January", "01"), ("Dec.", "12"),
    ("Spring", "21"), ("Summer", "22"), ("Autumn", "23"),
    ("Fall", "23"), ("Winter", "24"),
])
def test_chron_unit_codes(raw, code):
    """Autumn and Fall are the same season and must collapse to one code."""
    assert chron_unit_code(raw) == code


def test_unrecognised_chron_unit_is_left_alone():
    """
    Unknown text is passed through rather than dropped or guessed at, so nothing
    is invented. Normalisation still strips a trailing period, which is why this
    returns "Spr" rather than the "Spr." the source comment suggests.
    """
    assert chron_unit_code("Spr.") is None
    assert normalise_chron_unit("Spr.") == "Spr"
    assert normalise_chron_unit("Michaelmas") == "Michaelmas"


# ---------------------------------------------------------------------------
# Known defects
#
# These state intended behaviour and currently fail. They are non-strict, so
# fixing the parser reports XPASS rather than breaking the build -- at which
# point the marker should be removed.
# ---------------------------------------------------------------------------

@pytest.mark.xfail(reason="captionless leading volume is not recognised; "
                          "'v.39 no 1 (Spring 1995)' parses but '39 no 1 (Spring 1995)' does not")
def test_captionless_leading_volume_should_parse():
    """
    Both holdings statements on record 4 of data/example_holdings.mrc take this
    shape, and both are lost today. Adding a "v." caption is enough to make the
    same statement parse, so the defect is the missing caption, not the season.
    """
    r = parse_866("39 no 1 (Spring 1995)")
    assert r.success is True
    start = r.ranges[0].start
    assert (start.vol, start.issue, start.year, start.month) == ("39", "1", "1995", "21")


@pytest.mark.xfail(reason="_split_ranges() does not treat a spaced slash as a "
                          "separator, so the second range is silently dropped")
def test_slash_separated_ranges_should_both_survive():
    """
    The converter keeps only the first range of a slash-separated statement and
    reports success with no warning, so holdings the library owns vanish from
    the generated 863s. split_multi_range() in the detector was fixed for this
    in 0.5.1; _split_ranges() here has the same gap with worse consequences.
    """
    r = parse_866("v.1(1990)-v.3(1992) / v.5(1994)-v.8(1997)")
    assert len(r.ranges) == 2
    assert r.ranges[1].start.vol == "5"
    assert r.ranges[1].end.vol == "8"


@pytest.mark.xfail(reason="a brace note defeats the block grammar entirely")
def test_cataloguer_note_should_not_cost_the_statement():
    """
    "1993: {Memorial Issue} (1 [Feb])" warns that the note was preserved and
    then fails to find a block, returning zero ranges. The note should be
    reported and the holdings parsed, as happens for unexplained markers.
    """
    r = parse_866("1993: {Memorial Issue} (1 [Feb])")
    assert r.success is True
    assert len(r.ranges) == 1
    assert r.ranges[0].start.year == "1993"
