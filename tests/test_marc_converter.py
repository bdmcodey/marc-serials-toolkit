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
        # Chronology levels are named; enumeration is a sequence of positions.
        # A convention need not offer every chronology level -- the house one
        # has no day subfield -- but it may not invent levels either.
        assert set(preset["subfields"]) <= set(CONVENTION_LEVELS) | {"enum"}
        assert {"enum", "year", "month"} <= set(preset["subfields"])
        assert len(preset["subfields"]["enum"]) >= 3


def test_standard_and_house_differ_where_expected():
    standard, _ = resolve_convention("standard")
    house, _ = resolve_convention("house")

    # MARC 21 puts enumeration first; local practice leads with the year.
    assert standard["subfields"]["enum"][0] == "a"
    assert standard["subfields"]["year"] == "i"
    assert house["subfields"]["year"] == "a"
    assert house["subfields"]["enum"][0] == "b"
    assert standard["indicators"] == ("3", "1")
    assert house["indicators"] == ("2", "0")
    # House practice writes chronology as text ("Mar") rather than a code.
    assert standard["chron_as_text"] is False
    assert house["chron_as_text"] is True


def test_unknown_convention_falls_back_to_standard():
    spec, _ = resolve_convention("nonsense")
    assert spec == resolve_convention("standard")[0]


def test_valid_override_is_applied():
    spec, rejections = resolve_convention("standard", subfields={"year": "l"})
    assert spec["subfields"]["year"] == "l"
    assert rejections == []


def test_a_convention_without_a_day_subfield_says_so_rather_than_inventing_one():
    """
    MARC 21 puts the third chronology level in $k. The house convention
    reproduces local records that have no precedent for one, so it has no day
    slot at all -- and a statement carrying a day is named rather than quietly
    levelled off to the month.
    """
    standard, _ = resolve_convention("standard")
    house, _ = resolve_convention("house")
    assert standard["subfields"]["day"] == "k"
    assert "day" not in house["subfields"]


def test_enumeration_can_be_reseated_as_a_whole_sequence():
    """
    Enumeration has no level names to override one at a time, so a caller
    states the sequence: first level first.
    """
    spec, rejections = resolve_convention("standard", subfields={"enum": ("c", "d", "e")})
    assert spec["subfields"]["enum"] == ("c", "d", "e")
    assert rejections == []


def test_a_single_enumeration_level_can_be_moved_by_position():
    """
    A screen showing only the first few levels patches those positions and
    leaves the convention's depth alone.  "e1" is the first level.
    """
    spec, rejections = resolve_convention("standard", subfields={"enum": {"e1": "g"}})
    assert spec["subfields"]["enum"] == ("g", "b", "c", "d", "e", "f")
    assert rejections == []


def test_the_old_level_names_still_reach_their_positions():
    """
    Settings saved against the three-level model name vol/issue/part.  Those
    are positions 1-3 now, and a stored setting should not quietly stop
    working.
    """
    spec, rejections = resolve_convention("standard", subfields={"issue": "g"})
    assert spec["subfields"]["enum"] == ("a", "g", "c", "d", "e", "f")
    assert rejections == []


@pytest.mark.parametrize("subfields, expected_in_message", [
    ({"enum": ("z", "b")}, "$a-$m"),       # outside _ALLOWED_SUBFIELDS
    ({"enum": ("ab", "b")}, "$a-$m"),      # not a single character
    ({"enum": ("", "b")},  "$a-$m"),       # empty
    ({"enum": ("b", "b")}, "would carry two enumeration levels"),
    ({"enum": ("i", "b")}, "already used by chronology"),
    ({"year": "z"},        "$a-$m"),
    ({"year": "a"},        "already used by enumeration"),
    ({"month": "i"},       "already used by year"),
])
def test_bad_subfield_codes_are_rejected_back_to_the_preset(
        subfields, expected_in_message):
    """
    Every rejection must do two things: keep the safe value, and say what
    happened. A silent fallback would be as dangerous as accepting the code.
    """
    preset = convention_presets()["standard"]["subfields"]
    spec, rejections = resolve_convention("standard", subfields=subfields)
    assert spec["subfields"] == preset
    assert len(rejections) == 1
    assert expected_in_message in rejections[0]


def test_unknown_level_is_reported_not_added():
    spec, rejections = resolve_convention("standard", subfields={"bogus": "a"})
    assert "bogus" not in spec["subfields"]
    assert "Unknown level 'bogus'" in rejections[0]


def test_reassigning_a_level_to_its_current_code_is_not_a_collision():
    """year already holds $i; setting it to $i again is a no-op, not a clash."""
    spec, rejections = resolve_convention("standard", subfields={"year": "i"})
    assert spec["subfields"]["year"] == "i"
    assert rejections == []


def test_resolving_never_mutates_the_stored_presets():
    """
    A shallow copy here would let one request's overrides leak into every later
    request in a long-lived process -- the worst kind of bug to reproduce.
    """
    before = convention_presets()["standard"]["subfields"]
    resolve_convention("standard", subfields={"year": "k", "enum": {"e1": "g"}})
    assert convention_presets()["standard"]["subfields"] == before


# ---------------------------------------------------------------------------
# Caption helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("caption, slot", [
    # Enumeration captions are words, and MARC 21 does not say which words --
    # so every one of them is simply "an enumeration level".
    ("v.", "enum"), ("vol.", "enum"), ("Volume", "enum"),
    ("no.", "enum"), ("number", "enum"), ("iss.", "enum"),
    ("pt.", "enum"), ("Report no.", "enum"),
    ("(year)", "year"),
    ("(month)", "month"), ("(season)", "month"),
    ("", None), ("$x", None),
])
def test_caption_slot_tells_enumeration_from_chronology(caption, slot):
    assert caption_slot(caption) == slot


def test_read_853_slots_ignores_the_linking_subfield():
    """$8 is linking, not a caption, and must never be read as a level."""
    slots = read_853_slots(_existing_853(("8", "3"), ("a", "(year)"),
                                         ("b", "v."), ("c", "no.")))
    # Enumeration keeps the order the field states it in; chronology is named.
    assert slots == {"year": "a", "enum": ("b", "c")}


def test_read_853_captions_keeps_the_words_the_field_uses():
    """
    Conforming to an existing 853 means reusing its captions, whatever words
    it chose -- "Report no." is as valid a caption as "no.".
    """
    from marc_converter import read_853_captions
    captions = read_853_captions(_existing_853(("8", "3"), ("a", "Bd."),
                                               ("b", "Heft"), ("i", "(year)"),
                                               ("w", "m")))
    assert captions == ["Bd.", "Heft"]


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


def test_a_month_only_one_end_names_is_dropped():
    """
    "(1981 - Sep 1996)" says nothing about a month at the start.

    "$j 09-09" would invent one, and "$j 09" is no better: a reader pairs the
    subfields positionally, so "$i 1981-1996 $j 09" says the run begins in
    September 1981. There is no notation for a chronology belonging to one end
    only, so it is dropped and named. Distinguishable from the test above only
    inside _parse_chron, which is why the rule lives there.
    """
    result = convert_holdings(parse_866("v. 78 - v. 93 no. 3 (1981 - Sep 1996)"))
    assert sub(result.fields_863[0], "i") == "1981-1996"
    assert sub(result.fields_863[0], "j") is None
    assert any("(09)" in w for w in result.warnings), result.warnings


def test_a_season_only_the_start_names_is_dropped():
    """
    The same rule pointing the other way, and the reason it is symmetric.

    "v. 118 no. 1 (Spring 2012)-v. 122 no. 1 (2016)": both boundaries carry
    their own parenthesis and only the first names a season, so "$j 21" under
    "$i 2012-2016" would read as the whole run being Spring.
    """
    result = convert_holdings(
        parse_866("v. 118 no. 1 (Spring 2012)-v. 122 no. 1 (2016)"))
    assert sub(result.fields_863[0], "i") == "2012-2016"
    assert sub(result.fields_863[0], "j") is None
    assert any("(21)" in w for w in result.warnings), result.warnings


def test_a_start_value_still_stands_when_there_is_no_second_boundary():
    """
    The guard above must not swallow an ordinary single-unit statement, where
    there is no other end for the value to disagree with.
    """
    result = convert_holdings(parse_866("v. 58 (Sep 2003)"))
    assert sub(result.fields_863[0], "i") == "2003"
    assert sub(result.fields_863[0], "j") == "09"
    assert result.warnings == []


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
    assert any("(3)" in w and "no" in w for w in result.warnings), result.warnings


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


def test_a_year_stated_once_for_a_run_within_it_is_kept():
    """
    The block grammar's end boundary carries only a closing month, so every
    statement it produces looks like "the start states a year the end does not".
    Nothing above the year ranges, though, so "$i 1983 $j 01-12" is exactly
    right and dropping the year would be a regression -- which is what an
    earlier draft of this rule did.
    """
    result = convert_holdings(parse_866("1983: 5 (7-30 [Jan 28-Dec 29])"))
    f863 = result.fields_863[0]
    assert sub(f863, "i") == "1983"
    assert sub(f863, "j") == "01-12"


def test_a_range_inside_one_boundary_is_not_mistaken_for_a_pair():
    """
    "nos. 1-2" sits inside the end boundary and says nothing about where the run
    starts, so it is dropped like any other one-sided value -- unlike the
    "01-01" of "(Jan 1956 - Jan 1957)", which really is both endpoints. Both
    arrive as a lone hyphenated string; what separates them is whether the other
    boundary states anything at all at that level.
    """
    result = convert_holdings(parse_866("v. 1 (1956) - v. 51 nos. 1-2 (2006)"))
    assert sub(result.fields_863[0], "a") == "1-51"
    assert sub(result.fields_863[0], "b") is None
    assert any("(1-2)" in w for w in result.warnings), result.warnings


# ---------------------------------------------------------------------------
# A coded subfield holds codes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, dropped", [
    # A named issue that merely sits where chronology usually goes.
    ("v. 15 (1998 Buyers Guide)", "Buyers Guide"),
    # A season MARC has no code for, mixed with two it does.
    ("v. 15 no. 6 - v. 23 nos. 2/3 (Nov/Dec 1994 - Late Summer 2002)",
     "Late Summer"),
])
def test_wording_never_reaches_a_coded_chronology_subfield(text, dropped):
    """
    The 853 labels $j "(month)" or "(season)", so an 863 under it holds 01-12 or
    21-24. Unrecognised wording used to go through unchanged -- and
    "$j 11/12-Late Summer" put codes and prose in one subfield. The value is
    left out and named instead.
    """
    result = convert_holdings(parse_866(text))
    assert sub(result.fields_863[0], "j") is None
    assert any(dropped in w for w in result.warnings), result.warnings


def test_an_abbreviated_season_is_coded_rather_than_dropped():
    """
    The guard above only helps if the codes table is complete enough. "Sum" is
    Summer, and coding it is what keeps it out of the guard's way.
    """
    result = convert_holdings(parse_866("2018: ([Sum])"))
    assert sub(result.fields_863[0], "j") == "22"
    assert result.warnings == []


def test_a_day_level_date_keeps_its_month_and_year():
    """
    "Apr 18, 1996" matched none of _parse_chron_single's alternatives -- each
    wants the year adjacent to the month -- so the boundary returned
    (None, None) and "$i 1997" alone claimed the run began in 1997. The day is
    still not encoded (863 $k is unmodelled) but it is named, and the month and
    year survive.
    """
    result = convert_holdings(
        parse_866("v. 34 no. 8/9-v. 35 no. 23/24 (Apr 18, 1996-Dec 1997)"))
    f863 = result.fields_863[0]
    assert sub(f863, "i") == "1996-1997"
    assert sub(f863, "j") == "04-12"
    assert any("(18)" in w for w in result.warnings), result.warnings


def test_a_value_whose_level_the_853_contradicts_is_left_out_and_named():
    """
    One 853 is the caption pattern for every 863 linked to it. Two statements
    numbering by different hierarchies cannot share one, and writing the second
    one's value anyway would file "no. 5" under "v." -- read downstream as
    volume 5, wrong in a way nothing could detect.
    """
    result = convert_holdings(parse_866("v.1(1990), no.5(1995)"))

    assert result.field_853.display() == "853 31 $8 1 $a v. $i (year)"
    assert sub(result.fields_863[0], "a") == "1"
    assert sub(result.fields_863[1], "a") is None      # the 5 is not written
    assert any("no.5" in w and "calls that level 'v.'" in w
               for w in result.warnings), result.warnings


def test_a_three_level_statement_fills_three_enumeration_subfields():
    """
    Enumeration is positional and MARC 21 gives it $a-$f, so nothing about the
    third level is special -- it is simply the third caption declared.
    """
    result = convert_holdings(parse_866("v. 1 no. 2 pt. 3 (1990)-v. 4 no. 5 pt. 6 (1995)"))

    assert result.field_853.display() == \
        "853 31 $8 1 $a v. $b no. $c pt. $i (year)"
    assert result.fields_863[0].display() == \
        "863 40 $8 1.1 $a 1-4 $b 2-5 $c 3-6 $i 1990-1995"


def test_a_statement_numbered_only_by_issue_starts_at_the_first_subfield():
    """
    D6. "no. 26 (May 1994)" has no volume, and MARC 21 runs captions from $a
    downwards in descending significance -- so its issue level is $a no., not
    an empty $a with the issue pushed into $b.
    """
    result = convert_holdings(parse_866("no. 26 (May 1994)-no. 37 (May 2000)"))

    assert result.field_853.display() == \
        "853 31 $8 1 $a no. $i (year) $j (month)"
    assert result.fields_863[0].display() == \
        "863 40 $8 1.1 $a 26-37 $i 1994-2000 $j 05-05"


def test_a_level_the_screen_cannot_see_yields_to_one_it_can():
    """
    The settings dialog edits three enumeration levels; the standard convention
    has six. Moving the first onto $d collides with the fourth, which the
    cataloguer never saw. Their choice wins, the unseen level drops out, and
    the depth that costs is reported rather than left to be discovered.
    """
    spec, rejections = resolve_convention("standard", subfields={"enum": {"e1": "d"}})
    assert spec["subfields"]["enum"] == ("d", "b", "c", "e", "f")
    assert any("room for 5 levels" in r for r in rejections)


# ---------------------------------------------------------------------------
# Enumeration depth: the difference between a hierarchy and a list
# ---------------------------------------------------------------------------

def test_implausible_enumeration_depth_is_flagged_not_refused():
    """
    D14. "8,13,15,17,19,20-(1982-1994)" is six separate holdings. Read as a
    hierarchy it becomes $a 8 $b 13 $c 15 $d 17 $e 19 $f 20 -- one issue,
    numbered six levels deep, which is not a loss but an invention.

    Nothing here can tell a genuinely deep serial from a list once the values
    are in hand, so the record is still produced. What it must not do is
    produce it in silence: an error a cataloguer can catch is worth far more
    than one they cannot.
    """
    from marc_converter import _check_enumeration_depth

    warnings = []
    levels = {"enum_captions": ["v.", "no.", "pt.", "ser.", None, None]}
    assert _check_enumeration_depth(levels, warnings) is True
    assert any("separate holdings" in w for w in warnings)


@pytest.mark.parametrize("captions", [
    ["v."],
    ["no."],
    ["v.", "no."],
    ["ser.", "v.", "no."],          # the deepest the corpus reaches, once
])
def test_ordinary_enumeration_depth_is_not_flagged(captions):
    """
    89 of the corpus's ranges use two levels and one uses three. A guard that
    fired on those would be noise, and noise is how a real warning gets missed.
    """
    from marc_converter import _check_enumeration_depth

    warnings = []
    assert _check_enumeration_depth({"enum_captions": captions}, warnings) is False
    assert warnings == []


def test_a_three_level_statement_converts_unflagged():
    result = convert_holdings(parse_866("v. 1 no. 2 pt. 3 (1990)-v. 4 no. 5 pt. 6 (1995)"))
    assert result.flagged is False
    assert result.fields_863[0].display() == \
        "863 40 $8 1.1 $a 1-4 $b 2-5 $c 3-6 $i 1990-1995"


def test_a_flagged_record_still_carries_its_fields():
    """
    Flagged is not held. The cataloguer needs to see what the tool would write
    in order to judge it -- withholding the fields would hide the evidence.
    """
    from marc_converter import _check_enumeration_depth
    warnings = []
    assert _check_enumeration_depth(
        {"enum_captions": ["v.", "no.", "pt.", "ser."]}, warnings) is True
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# Day-level chronology in 863 $k
# ---------------------------------------------------------------------------

def test_a_day_reaches_the_third_chronology_subfield():
    """MARC 21 863: $i first chronology level, $j second, $k third."""
    result = convert_holdings(parse_866("1983: 5 (7-30 [Jan 28-Dec 29])"))

    assert result.field_853.display() == \
        "853 31 $8 1 $a v. $b no. $i (year) $j (month) $k (day)"
    assert result.fields_863[0].display() == \
        "863 40 $8 1.1 $a 5 $b 7-30 $i 1983 $j 01-12 $k 28-29"


def test_a_convention_with_no_day_subfield_names_the_day_it_cannot_place():
    """
    The house convention reproduces local records with no precedent for a day
    subfield, so it has none. Levelling the day off in silence is the one
    thing not to do.
    """
    spec, _ = resolve_convention("house")
    result = convert_holdings(parse_866("1983: 5 (7-30 [Jan 28-Dec 29])"),
                              convention_spec=spec)

    assert "$k" not in result.field_853.display()
    assert any("no subfield for a day" in w for w in result.warnings), result.warnings


def test_the_853_declares_no_day_a_convention_cannot_write():
    """An 853 caption its own 863s never fill describes a level that is not there."""
    spec, _ = resolve_convention("house")
    result = convert_holdings(parse_866("1983: 5 (7-30 [Jan 28-Dec 29])"),
                              convention_spec=spec)
    assert "(day)" not in result.field_853.display()


def test_a_statement_with_no_day_declares_none():
    result = convert_holdings(parse_866("v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)"))
    assert result.field_853.display() == \
        "853 31 $8 1 $a v. $b no. $i (year) $j (month)"
    assert "$k" not in result.fields_863[0].display()


def test_the_day_caption_is_not_read_as_an_enumeration_level():
    """
    "(day)" is short and wordlike, so without an explicit test it falls through
    to the enumeration branch and an existing 853's $k comes back as a
    numbering level.
    """
    assert caption_slot("(day)") == "day"
    slots = read_853_slots(_existing_853(("8", "1"), ("a", "v."), ("i", "(year)"),
                                         ("j", "(month)"), ("k", "(day)")))
    assert slots == {"enum": ("a",), "year": "i", "month": "j", "day": "k"}
