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
    assert (start.value_at(0), start.value_at(1), start.year, start.month) == ("1", "1", "1990", "01")
    assert (end.value_at(0), end.value_at(1), end.year, end.month) == ("5", "4", "1994", "12")
    assert r.ranges[0].open_ended is False


def test_caption_variants_and_seasons_parse_identically():
    """
    "Vol. 1, No. 1 (Spring 1990)" must reach the same structure as the terse
    form. Seasons become MARC season codes (21 Spring .. 24 Winter), not months.
    """
    r = parse_866("Vol. 1, No. 1 (Spring 1990)-Vol. 5, No. 4 (Winter 1994)")
    assert len(r.ranges) == 1

    start, end = r.ranges[0].start, r.ranges[0].end
    assert (start.value_at(0), start.value_at(1), start.year, start.month) == ("1", "1", "1990", "21")
    assert (end.value_at(0), end.value_at(1), end.year, end.month) == ("5", "4", "1994", "24")


def test_open_ended_range_has_no_end():
    """A trailing hyphen means "still being received", not a missing endpoint."""
    r = parse_866("v.6(1995)-")
    assert r.ranges[0].open_ended is True
    assert r.ranges[0].end is None
    assert r.ranges[0].start.value_at(0) == "6"


def test_compressed_range_keeps_both_endpoints_in_the_start_unit():
    """
    "v. 1-14 (1953-1966)" is a single unit whose values happen to be ranges, not
    two units either side of a separator: the hyphens sit inside the volume and
    the year rather than between two halves. The compressed values are carried
    through to the 863 verbatim, so this shape must not be "helpfully" split.
    """
    r = parse_866("v. 1-14 (1953-1966)")
    assert len(r.ranges) == 1
    assert r.ranges[0].start.value_at(0) == "1-14"
    assert r.ranges[0].start.year == "1953-1966"
    assert r.ranges[0].end is None
    assert r.ranges[0].open_ended is False


def test_multi_range_statement_splits_on_comma():
    r = parse_866("v.1(1990)-v.3(1992), v.5(1994)-")
    assert len(r.ranges) == 2
    assert r.ranges[0].start.value_at(0) == "1"
    assert r.ranges[0].end.value_at(0) == "3"
    assert r.ranges[1].start.value_at(0) == "5"
    assert r.ranges[1].open_ended is True


def test_year_only_range():
    r = parse_866("1990-1994")
    assert len(r.ranges) == 1
    assert r.ranges[0].start.year == "1990"
    assert r.ranges[0].end.year == "1994"
    assert r.ranges[0].start.value_at(0) is None


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
    """
    Positional role rule: inside the parens, a bare number is the issue.

    With no volume stated, that issue is the statement's only enumeration
    level, so it is the *first* one -- 853 captions run $a downwards from the
    most significant level present. The caption is what says it is an issue.
    """
    r = parse_866("1993: (1 [Feb])")
    assert len(r.ranges) == 1
    start = r.ranges[0].start
    assert [(lvl.caption, lvl.value) for lvl in start.enum] == [("no.", "1")]
    assert (start.year, start.month) == ("1993", "02")


def test_block_number_before_parens_is_a_volume():
    """The complementary rule: outside the parens, the number is the volume."""
    r = parse_866("1949: 1 (1-6 [Apr-Sep])")
    start, end = r.ranges[0].start, r.ranges[0].end
    assert (start.value_at(0), start.value_at(1), start.year) == ("1", "1-6", "1949")
    assert start.month == "04"
    assert end.month == "09"


def test_multi_year_block_run_on_yields_one_range_per_year():
    r = parse_866("2019: (1-6 [Feb-Nov])2020: (7-12 [Jan-Dec])")
    assert len(r.ranges) == 2
    assert [hr.start.year for hr in r.ranges] == ["2019", "2020"]
    assert r.ranges[0].start.month == "02"
    assert r.ranges[0].end.month == "11"
    assert [(lvl.caption, lvl.value) for lvl in r.ranges[1].start.enum] == [("no.", "7-12")]


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


@pytest.mark.parametrize("raw, code", [
    ("Spr.", "21"), ("Sum", "22"), ("Aut", "23"), ("Win", "24"),
])
def test_abbreviated_seasons_are_coded(raw, code):
    """
    Cataloguers abbreviate seasons as often as they abbreviate months, and the
    table held only the full words. An abbreviation used to fall through to
    normalise_chron_unit() and reach $j as prose; since the converter now
    refuses to write prose into a coded subfield, not coding these would mean
    dropping them.
    """
    assert chron_unit_code(raw) == code


def test_unrecognised_chron_unit_is_left_alone():
    """
    Text that is genuinely not a month or season is passed through rather than
    dropped or guessed at, so nothing is invented here. The converter decides
    separately whether it can be written -- see marc_converter._is_codeable.
    """
    assert chron_unit_code("Michaelmas") is None
    assert normalise_chron_unit("Michaelmas") == "Michaelmas"
    assert chron_unit_code("Buyers Guide") is None


# ---------------------------------------------------------------------------
# Known defects
#
# These state intended behaviour and currently fail. They are non-strict, so
# fixing the parser reports XPASS rather than breaking the build -- at which
# point the marker should be removed.
# ---------------------------------------------------------------------------

def test_captionless_leading_volume_parses():
    """
    Both holdings statements on record 4 of data/example_holdings.mrc take this
    shape. Adding a "v." caption was once enough to make the same statement
    parse, so the defect was the missing caption, not the season.

    A number sitting a level above an issue is a volume, which is what makes
    this readable without the caption. See the two guards in _parse_unit for
    what stops that reasoning being applied where it does not hold.
    """
    r = parse_866("39 no 1 (Spring 1995)")
    assert r.success is True
    start = r.ranges[0].start
    assert (start.value_at(0), start.value_at(1), start.year, start.month) == ("39", "1", "1995", "21")


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
    assert r.ranges[1].start.value_at(0) == "5"
    assert r.ranges[1].end.value_at(0) == "8"


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


# ---------------------------------------------------------------------------
# A captionless number is only a volume when the statement says so
# ---------------------------------------------------------------------------

def test_a_bare_number_with_no_issue_after_it_is_not_a_volume():
    """
    "2016?" is an uncertain year, not volume 2016. Without an issue caption to
    sit above, a leading number says nothing about its own level, and guessing
    would put a year into $a on every record of this shape.
    """
    r = parse_866("2016?")
    assert r.ranges[0].start.value_at(0) is None
    assert r.ranges[0].start.year == "2016"


def test_a_partly_readable_statement_is_left_alone_rather_than_half_converted():
    """
    "34 no 3, 4 (Summer, Autumn 1990)" reads as far as "34 no 3" and no further.
    Converting that much would be worse than converting nothing: the 866 is
    removed once anything has been written from it, so the second issue and both
    seasons would be deleted with it.
    """
    r = parse_866("34 no 3, 4 (Summer, Autumn 1990)")
    assert r.ranges == []
    assert r.success is False


def test_a_captioned_volume_is_unaffected_by_the_relaxed_caption():
    """The ordinary shapes must parse exactly as they did."""
    r = parse_866("v.39 no 1 (Spring 1995)")
    start = r.ranges[0].start
    assert (start.value_at(0), start.value_at(1), start.year, start.month) == ("39", "1", "1995", "21")


# ---------------------------------------------------------------------------
# Spacing around the range separator
# ---------------------------------------------------------------------------

def _shape(result):
    """A comparable summary of what a statement parsed to."""
    return [
        (r.start.value_at(0), r.start.value_at(1), r.start.year, r.start.month,
         None if r.end is None else (r.end.value_at(0), r.end.value_at(1), r.end.year, r.end.month),
         r.open_ended)
        for r in result.ranges
    ]


@pytest.mark.parametrize("tight, spaced", [
    ("v.1(1990)-v.5(1994)", "v. 1 (1990) - v. 5 (1994)"),
    ("v.1(1990)-v.5(1994)", "v.1(1990) - v.5(1994)"),
    ("v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)",
     "v.1:no.1 (1990:Jan.) - v.5:no.4 (1994:Dec.)"),
    ("v.1(1990)-", "v. 1 (1990) -"),
])
def test_spacing_around_the_separator_does_not_change_the_parse(tight, spaced):
    """
    The bug this pins was silent and destructive. _smart_split_range() compared
    the characters immediately either side of a candidate hyphen; with " - "
    both are spaces, no rule matched, and the statement parsed as a single unit.
    The end of the range was dropped, an 863 was produced for the start alone,
    and the source 866 was then removed as converted -- so the holdings were
    deleted with nothing on screen to say so.

    Writing a range with spaces around its separator is at least as common as
    writing it without.
    """
    assert _shape(parse_866(spaced)) == _shape(parse_866(tight))


def test_a_spaced_range_keeps_its_end():
    """The specific statement that surfaced it, asserted directly."""
    r = parse_866("v. 1 (2001) - v. 5 (2005)")
    assert len(r.ranges) == 1
    start, end = r.ranges[0].start, r.ranges[0].end
    assert (start.value_at(0), start.year) == ("1", "2001")
    assert end is not None, "the end of the range was dropped"
    assert (end.value_at(0), end.year) == ("5", "2005")


@pytest.mark.parametrize("text, vol, year", [
    ("v.1-5(1990-1994)", "1-5", "1990-1994"),
    ("v. 1-14 (1953-1966)", "1-14", "1953-1966"),
])
def test_a_compressed_range_is_still_one_unit(text, vol, year):
    """
    The other half of the rule. A hyphen between two digits joins two values at
    one level; it does not divide the statement. Reading the nearest non-space
    neighbours must not start splitting these.
    """
    r = parse_866(text)
    assert len(r.ranges) == 1
    assert r.ranges[0].start.value_at(0) == vol
    assert r.ranges[0].start.year == year
    assert r.ranges[0].end is None


# ---------------------------------------------------------------------------
# A unit is parsed whole or not at all
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, read, unread", [
    # A discontinuous list: the regex stops at the first comma.
    ("v. 19 nos. 1, 3, 5, 7-12 (Jan, Mar, May, Jul-Dec 1915)",
     "v. 19 nos. 1", ", 3, 5, 7-12 (Jan, Mar, May, Jul-Dec 1915)"),
    # A designation between the enumeration and the chronology.
    ("v. 58 Suppl. (Sep 2003)", "v. 58", "Suppl. (Sep 2003)"),
    ("v. 19 no. 2 Suppl. (1998)", "v. 19 no. 2", "Suppl. (1998)"),
])
def test_a_partly_matched_unit_converts_nothing(text, read, unread):
    """
    Converting part of a statement is worse than converting none of it.

    The Converter removes the 866 once anything has been written from it, so
    "v. 19 nos. 1, 3, 5, 7-12 (Jan, Mar, May, Jul-Dec 1915)" writing "$a 19
    $b 1" deleted eleven of the statement's twelve assertions with nothing on
    screen to say so.

    The guard that refuses a partial match already existed; it sat inside the
    branch for a number with no caption, so it fired for "34 no 3, 4 (...)" and
    never for the same shape with a "v." in front -- the common one. It now
    applies whenever the match does not account for the whole unit, and says
    how far it got.
    """
    result = parse_866(text)
    assert result.ranges == []
    assert result.success is False
    assert any(read in w and unread in w for w in result.warnings), result.warnings


def test_a_combined_volume_keeps_the_rest_of_its_statement():
    """
    "v.7/8" is a combined volume, the same shape iss_num has always accepted for
    issues. vol_num allowed only a hyphen, so the match stopped at "v.7" and the
    year and months were dropped -- silently, until the guard above turned it
    into a refusal. Widening vol_num converts it properly instead.
    """
    result = parse_866("v.7/8(1996:Jul./Aug.)")
    assert result.ranges[0].start.value_at(0) == "7/8"
    assert result.ranges[0].start.year == "1996"
    assert result.ranges[0].start.month == "07/08"


def test_a_month_at_one_end_of_a_chronology_group_is_not_kept():
    """
    "1981 - Sep 1996" names a month at one end only. A reader pairs subfields
    positionally, so keeping it would say the run begins in September 1981.
    Both ends naming a month is a different case and keeps both.
    """
    lone = parse_866("v. 78 - v. 93 no. 3 (1981 - Sep 1996)")
    assert lone.ranges[0].end.year == "1981-1996"
    assert lone.ranges[0].end.month is None

    paired = parse_866("v. 62 no. 1 - v. 63 no. 1 (Jan 1956 - Jan 1957)")
    assert paired.ranges[0].end.month == "01-01"


def test_a_series_designation_heads_its_statement_rather_than_splitting_it():
    """
    "Series 1, v. 6 no. 1 (Summer/Fall 1992)" is one statement three levels
    deep, not a range called "Series 1" beside a range called "v. 6 no. 1".

    Splitting it produced two ranges numbered by hierarchies no single 853 can
    describe, and the volume then landed under the series caption.
    """
    r = parse_866("Series 1, v. 6 no. 1 (Summer/Fall 1992)")
    assert len(r.ranges) == 1
    start = r.ranges[0].start
    assert [(lvl.caption, lvl.value) for lvl in start.enum] == [
        ("ser.", "1"), ("v.", "6"), ("no.", "1")]


def test_a_repeated_caption_after_a_comma_is_still_two_ranges():
    """
    The other side of that rule. "v. 1, v. 5 (1994)" numbers both sides the
    same way, so the comma divides two ranges -- which is how a cataloguer
    writes a gap in holdings.
    """
    r = parse_866("v. 1, v. 5 (1994)")
    assert len(r.ranges) == 2
    assert [hr.start.value_at(0) for hr in r.ranges] == ["1", "5"]


# ---------------------------------------------------------------------------
# Day-level chronology
# ---------------------------------------------------------------------------

def test_a_bracketed_day_range_keeps_both_days():
    """
    D10. "[Jan 28-Dec 29]" carries a month range and a day range, and the day
    half used to be discarded inside _bracket_chron_unit without a word. On a
    run-on statement that is fourteen dates gone from one record.
    """
    r = parse_866("1983: 5 (7-30 [Jan 28-Dec 29])")
    hr = r.ranges[0]
    assert (hr.start.month, hr.start.day) == ("01", "28")
    assert (hr.end.month, hr.end.day) == ("12", "29")


def test_two_days_in_one_month_still_make_a_range():
    """
    "[Jan 5-Jan 26]" is one month and two days. The end boundary used to be
    dropped when the months matched, which would now lose the second day.
    """
    r = parse_866("1984: 6 (1-4 [Jan 5-Jan 26])")
    hr = r.ranges[0]
    assert (hr.start.month, hr.start.day) == ("01", "5")
    assert (hr.end.month, hr.end.day) == ("01", "26")


def test_a_single_bracketed_date_has_no_end():
    r = parse_866("1984: 6 (6 [Feb 9])")
    hr = r.ranges[0]
    assert (hr.start.month, hr.start.day) == ("02", "9")
    assert hr.end is None


def test_a_day_in_the_enumeration_first_grammar_is_kept():
    """D4's other half: "Apr 18, 1996" now keeps the day rather than naming it."""
    r = parse_866("v.1(Apr 18, 1996)")
    assert (r.ranges[0].start.year, r.ranges[0].start.month,
            r.ranges[0].start.day) == ("1996", "04", "18")


def test_a_day_only_one_end_gives_is_dropped_and_named():
    """
    The same rule the month follows, and for the same reason: a lone value in
    $k pairs positionally with $i and $j, so it would claim the range ends on
    the 18th as well as beginning on it.
    """
    r = parse_866("v. 34 no. 8/9-v. 35 no. 23/24 (Apr 18, 1996-Dec 1997)")
    # The parens sit at the end of the statement, so the chronology group is
    # the end unit's -- the converter spreads it across both boundaries.
    chron = r.ranges[0].end
    assert chron.year == "1996-1997"
    assert chron.month == "04-12"
    assert chron.day is None
    assert any("day (18)" in w for w in r.warnings), r.warnings
