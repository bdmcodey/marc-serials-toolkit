"""
Tests for marc_converter: field generation, convention resolution, and $8 linking.

Two areas here carry more weight than their size suggests.

resolve_convention() is the guard against a bad subfield code reaching a record.
An invalid code would corrupt every record a run touches, silently and
identically, so the function rejects back to the preset rather than writing what
it was given -- and the rejection has to be reported, not swallowed.

convert_record() is the only place $8 is decided. MARC 21 treats the 853 as a
caption *pattern*, so a gap in holdings is another 863 under the same 853 rather
than a new one. Numbering therefore cannot be decided per statement, and the
tests below pin the cross-statement behaviour that follows from that.
"""

from __future__ import annotations

import pytest
from pymarc import Field, Subfield

from holdings_parser import parse_866
from marc_converter import (convention_presets, resolve_convention,
                            convert_holdings, convert_record,
                            caption_slot, read_853_slots,
                            CONVENTION_LEVELS)


def _existing_853(*pairs, indicators=("2", "0")) -> Field:
    """Build a pymarc 853 from (code, value) pairs, for the conform path."""
    return Field(tag="853", indicators=list(indicators),
                 subfields=[Subfield(code=c, value=v) for c, v in pairs])


def sub(field_data, code: str):
    """
    First value for `code` on a FieldData, or None.

    FieldData keeps subfields as an ordered list rather than a mapping, because
    MARC allows repeats and order is significant; this is just a reader for
    tests that care about one subfield.
    """
    for sf in field_data.subfields:
        if sf.code == code:
            return sf.value
    return None


def indicators(field_data) -> tuple:
    return (field_data.indicator1, field_data.indicator2)


# ---------------------------------------------------------------------------
# Presets and convention resolution
# ---------------------------------------------------------------------------

def test_both_presets_cover_every_level():
    """
    The settings dialog renders straight from this dict, so a missing level
    would show up as a silently unconfigurable caption rather than an error.
    """
    presets = convention_presets()
    assert set(presets) == {"standard", "house"}
    for preset in presets.values():
        assert set(preset["subfields"]) == set(CONVENTION_LEVELS)


def test_standard_and_house_differ_where_expected():
    standard, _ = resolve_convention("standard")
    house, _ = resolve_convention("house")

    # MARC 21 puts enumeration first; local practice leads with the year.
    assert standard["subfields"]["vol"] == "a"
    assert house["subfields"]["year"] == "a"
    assert standard["indicators"] == ("3", "1")
    assert house["indicators"] == ("2", "0")
    # House practice writes chronology as text ("Mar") rather than a code.
    assert standard["chron_as_text"] is False
    assert house["chron_as_text"] is True


def test_unknown_convention_falls_back_to_standard():
    spec, _ = resolve_convention("nonsense")
    assert spec == resolve_convention("standard")[0]


def test_valid_override_is_applied():
    spec, rejections = resolve_convention("standard", subfields={"vol": "d"})
    assert spec["subfields"]["vol"] == "d"
    assert rejections == []


@pytest.mark.parametrize("subfields, kept_level, kept_code, expected_in_message", [
    ({"vol": "z"},    "vol",   "a", "$a-$m"),      # outside _ALLOWED_SUBFIELDS
    ({"vol": "ab"},   "vol",   "a", "$a-$m"),      # not a single character
    ({"vol": ""},     "vol",   "a", "$a-$m"),      # empty
    ({"issue": "a"},  "issue", "b", "already used by vol"),   # collision
])
def test_bad_subfield_codes_are_rejected_back_to_the_preset(
        subfields, kept_level, kept_code, expected_in_message):
    """
    Every rejection must do two things: keep the safe value, and say what
    happened. A silent fallback would be as dangerous as accepting the code.
    """
    spec, rejections = resolve_convention("standard", subfields=subfields)
    assert spec["subfields"][kept_level] == kept_code
    assert len(rejections) == 1
    assert expected_in_message in rejections[0]


def test_unknown_level_is_reported_not_added():
    spec, rejections = resolve_convention("standard", subfields={"bogus": "a"})
    assert "bogus" not in spec["subfields"]
    assert "Unknown level 'bogus'" in rejections[0]


def test_reassigning_a_level_to_its_current_code_is_not_a_collision():
    """vol already holds $a; setting it to $a again is a no-op, not a clash."""
    spec, rejections = resolve_convention("standard", subfields={"vol": "a"})
    assert spec["subfields"]["vol"] == "a"
    assert rejections == []


def test_resolving_never_mutates_the_stored_presets():
    """
    A shallow copy here would let one request's overrides leak into every later
    request in a long-lived process -- the worst kind of bug to reproduce.
    """
    before = convention_presets()["standard"]["subfields"]["vol"]
    resolve_convention("standard", subfields={"vol": "d"})
    assert convention_presets()["standard"]["subfields"]["vol"] == before


# ---------------------------------------------------------------------------
# Caption helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("caption, level", [
    ("v.", "vol"), ("vol.", "vol"), ("Volume", "vol"),
    ("no.", "issue"), ("number", "issue"), ("iss.", "issue"),
    ("pt.", "part"),
    ("(year)", "year"),
    ("(month)", "month"), ("(season)", "month"),
    ("", None), ("$x", None),
])
def test_caption_slot_maps_display_captions_to_levels(caption, level):
    assert caption_slot(caption) == level


def test_read_853_slots_ignores_the_linking_subfield():
    """$8 is linking, not a caption, and must never be read as a level."""
    slots = read_853_slots(_existing_853(("8", "3"), ("a", "(year)"),
                                         ("b", "v."), ("c", "no.")))
    assert slots == {"year": "a", "vol": "b", "issue": "c"}


def test_read_853_slots_of_nothing_is_empty():
    assert read_853_slots(None) == {}


# ---------------------------------------------------------------------------
# Field generation
# ---------------------------------------------------------------------------

def test_standard_853_and_863_shape():
    result = convert_holdings(parse_866("v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)"))

    assert result.field_853.display() == "853 31 $8 1 $a v. $b no. $i (year) $j (month)"
    assert len(result.fields_863) == 1
    assert result.fields_863[0].display() == "863 40 $8 1.1 $a 1-5 $b 1-4 $i 1990-1994 $j 01-12"


def test_863_declares_itself_compressed():
    """
    Second indicator is Form of holdings: 0 compressed, 1 uncompressed.

    Every 863 this tool builds states a range -- the first part held and the
    last part held -- which is the definition of compressed. It carried 1 until
    September 2026, so each field asserted that its parts were itemised
    separately while holding "$a 1-5". Pinned because the value is a single
    character with no visible effect on the screen, and nothing else would
    notice it drifting back.
    """
    result = convert_holdings(parse_866("v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)"))
    assert indicators(result.fields_863[0]) == ("4", "0")


def test_house_convention_leads_with_the_year_and_writes_chronology_as_text():
    spec, _ = resolve_convention("house")
    result = convert_holdings(parse_866("v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)"),
                              convention_spec=spec)

    assert indicators(result.field_853) == ("2", "0")
    # Year in $a is the defining trait of local practice.
    assert sub(result.field_853, "a") == "(year)"
    # Months render as text rather than 01-12.
    assert "Jan" in result.fields_863[0].display()


def test_open_ended_range_renders_a_trailing_hyphen():
    result = convert_holdings(parse_866("v.6(1995)-"))
    display = result.fields_863[0].display()
    assert "$a 6-" in display
    assert "$i 1995-" in display


def test_season_chronology_uses_a_season_caption():
    """A season is not a month, and the 853 caption has to say so."""
    result = convert_holdings(parse_866("Vol. 1, No. 1 (Spring 1990)-Vol. 5, No. 4 (Winter 1994)"))
    assert "(season)" in result.field_853.display()


def test_statement_held_for_review_generates_no_fields():
    """
    A statement that could not be placed must produce nothing at all -- writing
    a partial 853 would be worse than leaving the 866 alone.
    """
    result = convert_holdings(parse_866("? 106"))
    assert result.field_853 is None
    assert result.fields_863 == []
    assert result.needs_review is True


def test_caption_overrides_beat_the_convention_defaults():
    result = convert_holdings(parse_866("v.1(1990)-v.3(1992)"),
                              captions={"vol": "tome"})
    assert "$a tome" in result.field_853.display()


def test_all_fields_omits_a_missing_853():
    result = convert_holdings(parse_866("? 106"))
    assert result.all_fields() == []


# ---------------------------------------------------------------------------
# $8 linking across a record
# ---------------------------------------------------------------------------

def test_statements_sharing_a_pattern_share_one_853():
    """
    Two runs of the same serial with a gap between them are one publication
    pattern. Before 0.5.0 each statement produced its own 853, so a record could
    carry four identical patterns under four linking numbers.
    """
    rc = convert_record([parse_866("v.1(1990)-v.10(1999)"),
                         parse_866("v.12(2001)-v.15(2004)")])

    assert len(rc.fields_853) == 1
    assert rc.links_written == ["1"]
    # Sequence runs across statements rather than restarting at .1 each time.
    assert [sub(f, "8") for f in rc.fields_863] == ["1.1", "1.2"]


def test_a_gap_in_holdings_does_not_start_a_new_853():
    """
    An 853 is a caption *pattern*. Missing volumes are a gap in what is held,
    not a change in how the serial is published, so both runs sit under one.
    """
    rc = convert_record([parse_866("v. 1 (2001)-v. 5 (2005)"),
                         parse_866("v. 7 (2007)")])

    assert len(rc.fields_853) == 1
    assert [sub(f, "8") for f in rc.fields_863] == ["1.1", "1.2"]


def test_a_statement_recording_less_detail_shares_the_853():
    """
    "v.5(1994)" beside "v.1:no.1(1990)" is the same publication with the issue
    simply not recorded, so they share an 853 -- the fuller of the two, since an
    863 need not fill every caption its 853 declares.

    This used to produce two 853s, splitting one publication in half.
    """
    rc = convert_record([parse_866("v.1:no.1(1990)-v.2:no.4(1991)"),
                         parse_866("v.5(1994)-v.8(1997)")])

    assert len(rc.fields_853) == 1
    assert sub(rc.fields_853[0], "b") == "no."     # the fuller 853 leads
    assert [sub(f, "8") for f in rc.fields_863] == ["1.1", "1.2"]
    # The sparser statement's 863 simply has no $b.
    assert sub(rc.fields_863[1], "b") is None


def test_the_fuller_853_leads_even_when_it_arrives_second():
    """Order of arrival must not decide which captions the run is described by."""
    rc = convert_record([parse_866("v.5(1994)-v.8(1997)"),
                         parse_866("v.1:no.1(1990)-v.2:no.4(1991)")])

    assert len(rc.fields_853) == 1
    assert sub(rc.fields_853[0], "b") == "no."


def test_a_different_chronology_gets_its_own_853():
    """
    Month and season chronology both use $j, with different captions, so they
    disagree on a caption they share and are two publication patterns -- not one
    recording less than the other. This is why compatibility is a subset test.
    """
    rc = convert_record([parse_866("v. 1 no. 1-4 (Mar-Dec 2001)"),
                         parse_866("v. 2 no. 1-4 (Winter-Fall 2002)")])

    assert len(rc.fields_853) == 2
    assert sub(rc.fields_853[0], "j") == "(month)"
    assert sub(rc.fields_853[1], "j") == "(season)"
    assert rc.links_written == ["1", "2"]


def test_a_pattern_that_returns_gets_a_new_linking_number():
    """
    The publication changed twice, and the record should say so. The months run
    after the season interruption is a third pattern, not a resumption of the
    first, so it takes the next linking number rather than reusing $8 1.

    Two of the three 853s are identical apart from $8. That is deliberate: it is
    not the fault v0.5.0 removed, which was one 853 per statement even where
    nothing had changed.
    """
    rc = convert_record([parse_866("v. 1 no. 1-4 (Mar-Dec 2001)"),
                         parse_866("v. 2 no. 1-4 (Winter-Fall 2002)"),
                         parse_866("v. 3 no. 1-4 (Mar-Dec 2003)")])

    assert len(rc.fields_853) == 3
    assert rc.links_written == ["1", "2", "3"]
    assert [sub(f, "8") for f in rc.fields_863] == ["1.1", "2.1", "3.1"]
    # First and third describe the same pattern and differ only in $8.
    first, third = rc.fields_853[0], rc.fields_853[2]
    assert [(x.code, x.value) for x in first.subfields if x.code != "8"] == \
           [(x.code, x.value) for x in third.subfields if x.code != "8"]


def test_a_statement_held_for_review_does_not_break_a_run():
    """
    Nothing is known about a statement that could not be read, so it is no
    evidence that the publication pattern changed. The runs either side of it
    stay one run.
    """
    rc = convert_record([parse_866("v.1(1990)-v.3(1992)"),
                         parse_866("? 106"),
                         parse_866("v.5(1994)-v.8(1997)")])

    assert len(rc.fields_853) == 1
    assert [sub(f, "8") for f in rc.fields_863] == ["1.1", "1.2"]


def test_conforming_adopts_the_existing_linking_number():
    """
    When the record already carries a usable 853, the converter reuses it rather
    than adding a competing one, and the 863s must point at the number it holds.
    """
    rc = convert_record([parse_866("v.1:no.1(1990)-v.2:no.4(1991)")],
                        existing_853s=[_existing_853(
                            ("8", "3"), ("a", "(year)"),
                            ("b", "v."), ("c", "no."))])

    assert rc.fields_853 == []          # nothing new written
    assert rc.conformed == 1
    assert rc.links_written == ["3"]
    assert sub(rc.fields_863[0], "8") == "3.1"


def test_review_statements_claim_no_linking_number():
    rc = convert_record([parse_866("? 106")])
    assert rc.needs_review == 1
    assert rc.converted == 0
    assert rc.links_written == []
    assert rc.fields_853 == []


def test_empty_record_converts_to_nothing():
    rc = convert_record([])
    assert rc.fields_853 == []
    assert rc.fields_863 == []
    assert rc.links_written == []
    assert (rc.converted, rc.conformed, rc.needs_review) == (0, 0, 0)


def test_record_warnings_are_deduplicated_in_order():
    """
    The same warning on five statements should be shown once. Order is preserved
    so the first thing that went wrong is still the first thing reported.
    """
    rc = convert_record([parse_866("2016?"), parse_866("2017?")])
    assert len(rc.warnings) == len(set(rc.warnings))


def test_every_member_of_a_run_reports_the_853_that_gets_written():
    """
    Only the run's fullest 853 reaches the record, so a member reporting the one
    it would have had alone would be previewing a field that is never written.
    The per-statement preview is what a cataloguer approves, so it has to show
    the field they will actually get.
    """
    rc = convert_record([parse_866("v.1:no.1(1990)-v.2:no.4(1991)"),
                         parse_866("v.5(1994)-v.8(1997)")])

    shown = [r.field_853.display() for r in rc.results]
    assert shown[0] == shown[1]
    assert "$b no." in shown[1], "the sparser statement should show the run's 853"


def test_a_merged_run_is_reported_as_merged():
    """
    Joining on a subset is the one grouping decision a cataloguer might not
    agree with, so the run says it happened rather than presenting the merge as
    settled.
    """
    rc = convert_record([parse_866("v.1:no.1(1990)-v.2:no.4(1991)"),
                         parse_866("v.5(1994)-v.8(1997)")])
    assert rc.merged_links == ["1"]

    # A run whose members match exactly was not merged, it simply agreed.
    plain = convert_record([parse_866("v.1(1990)-v.3(1992)"),
                            parse_866("v.5(1994)")])
    assert plain.merged_links == []


def test_keeping_patterns_separate_stops_the_merge():
    """
    Whether "v.5(1994)" beside "v.1:no.1(1990)" is one publication or two is a
    judgement about the serial, not about the strings. A cataloguer who knows it
    is two can say so.
    """
    stmts = [parse_866("v.1:no.1(1990)-v.2:no.4(1991)"),
             parse_866("v.5(1994)-v.8(1997)")]

    merged = convert_record(stmts)
    assert len(merged.fields_853) == 1

    separate = convert_record(
        [parse_866("v.1:no.1(1990)-v.2:no.4(1991)"),
         parse_866("v.5(1994)-v.8(1997)")], merge_patterns=False)
    assert len(separate.fields_853) == 2
    assert separate.links_written == ["1", "2"]
    assert separate.merged_links == []


def test_keeping_patterns_separate_does_not_disturb_runs():
    """
    The switch governs how much detail two statements may differ by, not whether
    a returning pattern gets a new number. Month/season/month is three runs
    either way.
    """
    stmts = ["v. 1 no. 1-4 (Mar-Dec 2001)",
             "v. 2 no. 1-4 (Winter-Fall 2002)",
             "v. 3 no. 1-4 (Mar-Dec 2003)"]
    for merge in (True, False):
        rc = convert_record([parse_866(s) for s in stmts], merge_patterns=merge)
        assert rc.links_written == ["1", "2", "3"], merge


# ---------------------------------------------------------------------------
# A compressed range carries both of its endpoints
# ---------------------------------------------------------------------------

def test_equal_endpoints_under_a_ranging_level_keep_both_ends():
    """
    "$a 41-43 $b 1" cannot be read back as v.41:no.1 - v.43:no.1.

    It describes issue 1 of each of volumes 41 to 43 just as well, and the
    pairing of the two endpoints is what a compressed 863 exists to carry.
    _enum_value() collapsed equal endpoints to a single value until 0.6.2.
    """
    result = convert_holdings(parse_866("v. 41 no. 1-v. 43 no. 1 (Jun 1984-Jan/Apr 1986)"))
    assert sub(result.fields_863[0], "b") == "1-1"
    assert sub(result.fields_863[0], "a") == "41-43"


def test_equal_endpoints_with_nothing_ranging_above_stay_single():
    """
    The converse, and the reason the rule is not "always repeat".

    "v. 43 no. 6 - v. 43 no. 7" is fully recoverable from "$a 43 $b 6-7": the
    volume does not range, so there is no pairing to lose and "$a 43-43" would
    be noise. Only a level *under* a ranging one repeats.
    """
    result = convert_holdings(
        parse_866("v. 43 no. 6 - v. 43 no. 7 (June 2022 - July/August 2022)"))
    assert sub(result.fields_863[0], "a") == "43"
    assert sub(result.fields_863[0], "b") == "6-7"
    assert sub(result.fields_863[0], "i") == "2022"


def test_a_value_that_is_already_a_range_is_not_repeated():
    """"no. 3-4 - no. 3-4" must not become the unreadable "3-4-3-4"."""
    result = convert_holdings(
        parse_866("v. 23 no. 3-4-v. 29 no. 3-4 (Fall 1985-Fall/Winter 1991)"))
    assert sub(result.fields_863[0], "b") == "3-4"


def test_both_ends_naming_the_same_month_keep_both():
    """
    The same rule one layer down, where a single chronology group spans the
    range: "(Jan 1956 - Jan 1957)" parses onto the end boundary alone, so
    _parse_chron is the only place that can still see both months. Collapsing
    there produced "$i 1956-1957 $j 01", one January across two years.
    """
    result = convert_holdings(parse_866("v. 62 no. 1 - v. 63 no. 1 (Jan 1956 - Jan 1957)"))
    assert sub(result.fields_863[0], "i") == "1956-1957"
    assert sub(result.fields_863[0], "j") == "01-01"


def test_a_month_only_one_end_names_is_not_repeated():
    """
    "(1981 - Sep 1996)" says nothing about a month at the start, so "$j 09-09"
    would invent one. Distinguishable from the test above only inside
    _parse_chron, which is why the fix lives there.
    """
    result = convert_holdings(parse_866("v. 78 - v. 93 no. 3 (1981 - Sep 1996)"))
    assert sub(result.fields_863[0], "j") == "09"


# ---------------------------------------------------------------------------
# A level only one boundary states is left out, and said so
# ---------------------------------------------------------------------------

def test_a_month_only_the_end_states_is_dropped_not_written_as_the_start():
    """
    Both boundaries are written out and only the second names a month, so
    December belongs to the end alone. Writing it asserted that the holdings
    *begin* in December 2006; there is no notation for half a range, so the
    level is left out -- and named, so the value is accounted for.
    """
    result = convert_holdings(parse_866("v. 1 no. 1 (1995)-v. 12 no. 4 (December 2006)"))
    f863 = result.fields_863[0]

    assert sub(f863, "a") == "1-12"
    assert sub(f863, "b") == "1-4"
    assert sub(f863, "i") == "1995-2006"
    assert sub(f863, "j") is None
    assert any("(12)" in w for w in result.warnings), result.warnings


def test_an_issue_only_the_end_states_is_dropped_and_named():
    """
    The same case for enumeration, which had no fallback at all and dropped the
    value in silence. The 853 still declares a `no.` caption, because the level
    is genuinely part of this publication's numbering -- what changed is that
    the record now says which issue it could not place.
    """
    result = convert_holdings(parse_866("v. 1 - v. 55 no. 3 (1927-1982)"))
    f863 = result.fields_863[0]

    assert sub(f863, "a") == "1-55"
    assert sub(f863, "b") is None
    assert any("(3)" in w and "issue" in w for w in result.warnings), result.warnings


def test_a_single_chronology_group_still_covers_the_whole_range():
    """
    The end-boundary fallback that D16 narrowed, still working where it should.

    "v.1:no.1-v.2:no.4(1990-1991)" has one chronology group for the whole
    statement, which the parser hangs on the end boundary. The start states no
    chronology at all, so those years describe both ends and are used as they
    stand -- no warning, nothing dropped.
    """
    result = convert_holdings(parse_866("v.1:no.1-v.2:no.4(1990-1991)"))
    assert sub(result.fields_863[0], "i") == "1990-1991"
    assert result.warnings == []
