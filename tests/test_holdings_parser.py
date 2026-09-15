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
                             normalise_chron_unit, normalise_year,
                             HoldingsRange, EnumChron, EnumLevel)


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
# Discontinuous lists
# ---------------------------------------------------------------------------

def test_a_discontinuous_list_is_one_range_per_run():
    """
    Four runs of holdings with gaps between them, written the compact way. MARC
    21 records gaps as separate 863s, so four runs are four ranges. The parser
    refused the whole statement before this -- correctly, since reading only
    "v. 19 no. 1" and removing the 866 would have deleted the other three runs.
    """
    r = parse_866("v. 19 nos. 1, 3, 5, 7-12 (Jan, Mar, May, Jul-Dec 1915)")
    assert [(hr.start.value_at(0), hr.start.value_at(1),
             hr.start.year, hr.start.month) for hr in r.ranges] == [
        ("19", "1", "1915", "01"),
        ("19", "3", "1915", "03"),
        ("19", "5", "1915", "05"),
        ("19", "7-12", "1915", "07-12"),
    ]


def test_a_year_stated_once_at_the_end_covers_every_run_before_it():
    """
    "(Jan, Mar, May, Jul-Dec 1915)" writes 1915 once, for all four. Reading the
    list right to left is what gets each item the year it is written under.
    """
    r = parse_866("v. 19 nos. 1, 3, 5, 7-12 (Jan, Mar, May, Jul-Dec 1915)")
    assert {hr.start.year for hr in r.ranges} == {"1915"}


def test_a_list_crossing_a_year_keeps_each_run_on_its_own_year():
    """The same rule, where the years differ: nearest year to the right."""
    r = parse_866("v. 19 nos. 1, 3 (Nov 1915, Jan 1916)")
    assert [(hr.start.month, hr.start.year) for hr in r.ranges] == [
        ("11", "1915"), ("01", "1916")]


def test_one_bare_year_is_stated_once_for_the_whole_list():
    """
    "(1915)" is not a list of one against a list of two -- it is the year every
    run in the statement falls in, and applies to all of them. "1915/16" is the
    same: one publication year written across the turn of one.
    """
    assert {hr.start.year for hr in parse_866("v. 19 nos. 1, 3 (1915)").ranges} \
        == {"1915"}
    assert {hr.start.year for hr in parse_866("v. 19 nos. 1, 3 (1915/16)").ranges} \
        == {"1915/1916"}


@pytest.mark.parametrize("text, dropped", [
    # A range spans the statement, not any one run in it. Writing it to each
    # 863 put twelve years on a single issue -- which is what this did until
    # the rule was narrowed from "a bare year" to "a single year".
    ("v. 19 nos. 1, 3, 5 (1982-1994)", "1982-1994"),
    # Anything more specific than a year cannot be true of every run either.
    ("v. 19 nos. 1, 3 (Jan 1915)", "Jan 1915"),
])
def test_a_chronology_that_cannot_be_shared_is_named_not_copied(text, dropped):
    """
    The enumeration is unambiguous and is kept; only the chronology has nowhere
    to go. Refusing the statement would throw away holdings the parser read
    perfectly well, and copying the chronology onto each run would assert
    something the statement never said -- so it is named, which is what this
    toolkit does with every other value it can read and cannot place.
    """
    result = parse_866(text)
    assert len(result.ranges) > 1
    assert all(hr.start.year is None for hr in result.ranges)
    assert any(dropped in w for w in result.warnings), result.warnings


def test_the_two_lists_have_to_be_the_same_length():
    """
    Pairing them is the whole claim. Three issue runs against two months means
    the statement was not understood, and a converter that carried on would file
    holdings under the wrong dates.
    """
    assert parse_866("v. 19 nos. 1, 3, 5 (Jan, Mar 1915)").ranges == []


def test_a_gap_between_runs_is_marked_and_a_continuation_is_not():
    """
    863 $w: "g" is a gap break -- parts lacking, or a break whose cause is not
    known, which is what listing "nos. 1, 3" records. Runs that follow straight
    on have no break to indicate.
    """
    gapped = parse_866("v. 19 nos. 1, 3, 5, 7-12 (Jan, Mar, May, Jul-Dec 1915)")
    assert [hr.break_after for hr in gapped.ranges] == ["g", "g", "g", ""]

    contiguous = parse_866("v. 19 nos. 1, 2, 3 (Jan, Feb, Mar 1915)")
    assert [hr.break_after for hr in contiguous.ranges] == ["", "", ""]


def test_a_list_can_sit_at_any_level():
    """
    Nothing here is about issues. The caption before the first item is what the
    later items inherit, whatever it is.
    """
    r = parse_866("v. 19, 20, 22 (1915, 1916, 1918)")
    assert [(hr.start.value_at(0), hr.start.year) for hr in r.ranges] == [
        ("19", "1915"), ("20", "1916"), ("22", "1918")]
    assert [hr.break_after for hr in r.ranges] == ["", "g", ""]


def test_a_list_with_no_caption_anywhere_is_still_a_list():
    """
    D7's statement. Nothing in "8,13,15,17,19,20-" says whether those are
    volumes, issues or years -- and nothing has to. They are the only
    enumeration level there is, and 0.8.9 writes "(*)" for a level with no
    caption rather than guessing at one, so the list can be read as six runs.

    The unit parser still refuses a lone captionless number, which is right: on
    its own it says nothing about which level it is. Inside a list it is not on
    its own.
    """
    r = parse_866("8,13,15,17,19,20-(1982-1994)")
    assert [hr.start.value_at(0) for hr in r.ranges] == \
        ["8", "13", "15", "17", "19", "20"]
    assert all(hr.start.enum[0].caption is None for hr in r.ranges)
    # 19 runs straight on into 20; every other break is a gap.
    assert [hr.break_after for hr in r.ranges] == ["g", "g", "g", "g", "", ""]


def test_a_bare_number_on_its_own_is_still_refused():
    """The guard the list reader must not have relaxed."""
    assert parse_866("106").ranges == []


def test_the_last_run_of_a_list_can_still_be_open():
    """
    "20-" is holdings still being received, and the hyphen saying so is the last
    thing before the chronology. Taken off before the list is split, and put
    back on the run built from it.
    """
    r = parse_866("8,13,15,17,19,20-(1982-1994)")
    assert [hr.open_ended for hr in r.ranges] == [False] * 5 + [True]


def test_a_captionless_list_reads_its_own_chronology():
    """The chronology side is unaffected by there being no caption."""
    r = parse_866("8,13,15 (1982, 1984, 1986)")
    assert [(hr.start.value_at(0), hr.start.year) for hr in r.ranges] == [
        ("8", "1982"), ("13", "1984"), ("15", "1986")]


@pytest.mark.parametrize("text, ranges", [
    # An American date puts a comma inside one date. Splitting there would turn
    # "Apr 18, 1996" into two holdings runs.
    ("v. 34 no. 8/9-v. 35 no. 23/24 (Apr 18, 1996-Dec 1997)", 1),
    # A comma between a volume and its issue caption is not a list separator.
    ("Vol. 1, No. 1 (Spring 1990)", 1),
    # Genuine multi-range statements were always split, and still are.
    ("v.1(1990)-v.3(1992), v.5(1994)-v.8(1997)", 2),
])
def test_a_comma_that_is_not_a_list_separator_is_left_alone(text, ranges):
    assert len(parse_866(text).ranges) == ranges


# ---------------------------------------------------------------------------
# Lining the two boundaries up
# ---------------------------------------------------------------------------

def test_an_end_stating_fewer_levels_slides_to_where_its_caption_fits():
    """
    Position in `enum` is the level, which holds only while both ends write the
    same number of levels. "v. 12 no. 1-no. 6" writes two and then one, so "6"
    sat at position 0 opposite "v. 12" and the converter read the range as
    volume 12 to volume 6. Its caption says otherwise and says so unambiguously,
    so an empty level goes in front of it.
    """
    hr = parse_866("v. 12 no. 1-no. 6 (1990)").ranges[0]
    assert [lvl.caption for lvl in hr.end.enum] == [None, "no."]
    assert (hr.end.value_at(0), hr.end.value_at(1)) == (None, "6")


def test_a_start_stating_fewer_levels_slides_the_same_way():
    """The rule is about the shorter boundary, not about which end it is."""
    start = EnumChron(enum=[EnumLevel("no.", "1")])
    end = EnumChron(enum=[EnumLevel("v.", "2"), EnumLevel("no.", "4")])
    hr = HoldingsRange(start=start, end=end)
    assert [lvl.caption for lvl in hr.start.enum] == [None, "no."]


def test_a_caption_that_fits_nowhere_moves_nothing():
    """
    Alignment is only ever allowed to act on evidence. "pt." appears at no level
    of a range numbered by volume and issue, so there is nothing to conclude and
    the boundary is left exactly where it was -- the converter then reports the
    value rather than placing it by position.
    """
    start = EnumChron(enum=[EnumLevel("v.", "1"), EnumLevel("no.", "1")])
    end = EnumChron(enum=[EnumLevel("pt.", "4")])
    hr = HoldingsRange(start=start, end=end)
    assert [lvl.caption for lvl in hr.end.enum] == ["pt."]


def test_a_caption_that_fits_twice_moves_nothing():
    """
    Two levels of the same name give two answers, and a wrong guess between them
    is invisible in the output. Silence is the safe half of the trade.
    """
    start = EnumChron(enum=[EnumLevel("v.", "1"), EnumLevel("pt.", "1"),
                            EnumLevel("pt.", "2")])
    end = EnumChron(enum=[EnumLevel("pt.", "9")])
    hr = HoldingsRange(start=start, end=end)
    assert [lvl.caption for lvl in hr.end.enum] == ["pt."]


def test_a_boundary_without_captions_is_left_alone():
    """
    "v. 1 no. 1-4" states its second value with no caption of its own. There is
    nothing to align by, and the existing reading -- position -- is the only one
    available.
    """
    start = EnumChron(enum=[EnumLevel("v.", "1"), EnumLevel("no.", "1")])
    end = EnumChron(enum=[EnumLevel(None, "4")])
    hr = HoldingsRange(start=start, end=end)
    assert [lvl.caption for lvl in hr.end.enum] == [None]


def test_aligning_twice_is_the_same_as_aligning_once():
    """
    The parser builds an empty range and fills it in, so alignment runs at
    construction and again afterwards. It has to be safe to repeat.
    """
    hr = parse_866("v. 12 no. 1-no. 6 (1990)").ranges[0]
    hr.align_boundaries()
    assert [lvl.caption for lvl in hr.end.enum] == [None, "no."]


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


def test_block_number_inside_parens_keeps_the_lower_position():
    """
    Positional role rule: the number before the parens is the higher level and
    the number inside is the lower one. That is all the format says, so it is
    all the parser reads -- and it means the lower one keeps its position when
    the higher is absent, rather than sliding up into it.

    Sliding was what happened, and it put the same level of one serial into two
    subfields: "N1984: (2 (1))M1985: 2 (2 [summer])" sent the 1984 issue to $a
    and the 1985 issue to $b, under an 853 reading "$a no. $b no." -- two levels
    with one name. An empty level now holds the place the block omits.

    Where *no* block in the statement states the higher level, there is nothing
    to be in step with and the placeholder is dropped, so this statement still
    reads as the one level it has. Declaring the other would put a level in the
    853 that the serial does not have.

    Neither level carries a caption. The format names neither, and the 853
    writes "(*)" for a level nobody has named.
    """
    r = parse_866("1993: (1 [Feb])")
    assert len(r.ranges) == 1
    start = r.ranges[0].start
    assert [(lvl.caption, lvl.value) for lvl in start.enum] == [(None, "1")]
    assert (start.year, start.month) == ("1993", "02")


def test_a_placeholder_level_survives_when_another_block_fills_it():
    """The other half: one block states the higher level, so it is a real one."""
    r = parse_866("N1984: (2 (1))M1985: 2 (2 [summer])")
    assert [[(l.caption, l.value) for l in hr.start.enum] for hr in r.ranges] == [
        [(None, None), (None, "2")],
        [(None, "2"), (None, "2")],
    ]


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
    assert [(lvl.caption, lvl.value) for lvl in r.ranges[1].start.enum] == [
        (None, "7-12")]


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


def test_slash_separated_ranges_both_survive():
    """
    A spaced slash separates two ranges. split_multi_range() in the detector has
    drawn this distinction since 0.5.1; _split_ranges() never did, and the
    consequences were worse than the xfail this replaces described.

    The statement reached _parse_unit() as one unit, whose end half could only
    be read as far as "v.3(1992)". That was refused -- correctly -- but the
    refusal only nulled the end, so the truncated start survived and the record
    got "$a 1 $i 1990": one volume out of the eight the statement names, with
    neither review nor a flag, under a warning claiming nothing had been
    written.
    """
    r = parse_866("v.1(1990)-v.3(1992) / v.5(1994)-v.8(1997)")
    assert len(r.ranges) == 2
    assert (r.ranges[0].start.value_at(0), r.ranges[0].end.value_at(0)) == ("1", "3")
    assert (r.ranges[1].start.value_at(0), r.ranges[1].end.value_at(0)) == ("5", "8")


@pytest.mark.parametrize("text, values", [
    # A bare slash is part of a value, and splitting on it would corrupt the
    # statement: a combined issue, a combined month, a split year.
    ("v.7/8(1996:Jul./Aug.)", ["7/8"]),
    ("v. 92 no. 1/2-v. 95 no. 1/2 (1993-1996)", ["92", "95"]),
])
def test_a_bare_slash_is_not_a_separator(text, values):
    r = parse_866(text)
    got = [hr.start.value_at(0) for hr in r.ranges]
    got += [hr.end.value_at(0) for hr in r.ranges if hr.end]
    assert [v for v in got if v] == values


def test_a_refused_end_unit_refuses_the_whole_range():
    """
    _parse_unit() refuses a unit it can only read part of, and its warning says
    "nothing was converted from this statement rather than convert part of it".
    Keeping the start made that untrue. The message is the promise; this is the
    behaviour matching it.

    Held against a statement the spaced-slash fix does not reach, so the two
    changes are tested apart: "Suppl." is what stops the end unit here.
    """
    r = parse_866("v. 1 (1990)-v. 3 Suppl. (1992)")
    assert r.ranges == []
    assert any("could not account for" in w for w in r.warnings), r.warnings


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


def test_a_captionless_list_is_read_the_same_way_a_captioned_one_is():
    """
    "34 no 3, 4 (Summer, Autumn 1990)" used to read as far as "34 no 3" and no
    further, and refused the statement rather than converting a third of it --
    the 866 is removed once anything has been written from it, so the second
    issue and both seasons would have been deleted with it.

    It is a two-run list, and is now read as one. The captionless leading number
    is unchanged by any of this: "34 no 3 (Summer 1990)" on its own has always
    been read as v.34 no.3, and the list form now agrees with it.
    """
    r = parse_866("34 no 3, 4 (Summer, Autumn 1990)")
    assert len(r.ranges) == 2
    assert [(hr.start.value_at(0), hr.start.value_at(1), hr.start.month)
            for hr in r.ranges] == [("34", "3", "22"), ("34", "4", "23")]
    # Issue 3 runs straight on into issue 4, so there is no break to indicate.
    assert [hr.break_after for hr in r.ranges] == ["", ""]


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
    # A designation between the enumeration and the chronology.
    ("v. 58 Suppl. (Sep 2003)", "v. 58", "Suppl. (Sep 2003)"),
    ("v. 19 no. 2 Suppl. (1998)", "v. 19 no. 2", "Suppl. (1998)"),
    # A discontinuous list whose two halves do not pair: three issue runs
    # against two months. The list is read as a list, then refused as a whole
    # rather than paired off in the order they happen to appear.
    ("v. 19 nos. 1, 3, 5 (Jan, Mar 1915)",
     "v. 19 nos. 1", ", 3, 5 (Jan, Mar 1915)"),
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

    That statement is no longer among the cases here: the parser reads a
    discontinuous list properly now, one 863 per run. The guard is what still
    stands behind the lists it cannot read whole.
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


# ---------------------------------------------------------------------------
# Years split across the turn of one
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("1996", "1996"),
    ("1996/97", "1996/1997"),
    ("1996/1997", "1996/1997"),
    ("1996 / 97", "1996/1997"),
    ("1999/00", "1999/2000"),      # rolls forward over the century
    ("1899/00", "1899/1900"),
    ("2019/20", "2019/2020"),
])
def test_a_split_year_is_written_out_in_full(raw, expected):
    """
    A serial whose winter issue straddles the new year is numbered "1996/97".
    MARC records the pair in $i slash-joined with both halves in full, the same
    way it records a combined month in $j.
    """
    assert normalise_year(raw) == expected


def test_a_split_year_parses_as_one_year():
    r = parse_866("v. 12 no. 4 (Winter 1996/97)")
    start = r.ranges[0].start
    assert (start.year, start.month) == ("1996/1997", "24")


def test_a_split_year_at_the_end_of_a_range():
    """
    The shape that reported this: the whole end boundary used to fail to parse,
    so the year was dropped as unreadable wording and the season with it -- the
    range came out claiming to be all Spring.
    """
    r = parse_866("v.1(Spring 1996)-v.5(Winter 1996/97)")
    hr = r.ranges[0]
    assert (hr.start.year, hr.start.month) == ("1996", "21")
    assert (hr.end.year, hr.end.month) == ("1996/1997", "24")


def test_a_bare_split_year_needs_no_season():
    r = parse_866("v.8(1996/1997)")
    assert r.ranges[0].start.year == "1996/1997"
