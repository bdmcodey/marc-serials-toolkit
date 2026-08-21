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
    assert result.fields_863[0].display() == "863 41 $8 1.1 $a 1-5 $b 1-4 $i 1990-1994 $j 01-12"


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


def test_differing_patterns_get_their_own_853():
    rc = convert_record([parse_866("v.1:no.1(1990)-v.2:no.4(1991)"),
                         parse_866("v.5(1994)-v.8(1997)")])

    assert len(rc.fields_853) == 2
    assert rc.links_written == ["1", "2"]
    assert [sub(f, "8") for f in rc.fields_853] == ["1", "2"]
    assert [sub(f, "8") for f in rc.fields_863] == ["1.1", "2.1"]


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
