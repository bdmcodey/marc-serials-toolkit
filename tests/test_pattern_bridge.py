"""
The join between the two tools: a detector regex plus a cataloguer's decisions
becoming the same ParseResult the parser produces.

The inference tests are the important half. A role assigned wrongly does not
fail loudly -- it writes a plausible-looking value into the wrong subfield of
every record the pattern touches. So the defaults are pinned here statement by
statement, including the ones the detector itself gets wrong and position gets
right.
"""

from __future__ import annotations

import re

import pytest

from holdings_parser import parse_866
from pattern_detector import detect_patterns
from pattern_bridge import (
    BOUNDARY_END,
    BOUNDARY_START,
    LEVEL_IGNORE,
    LEVEL_ISSUE,
    LEVEL_MONTH,
    LEVEL_UNRESOLVED,
    LEVEL_VOL,
    LEVEL_YEAR,
    apply_patterns,
    build_parse_result,
    infer_roles,
    merge_roles,
    roles_from_regex,
)
import pattern_library as plib


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def detect_one(statement: str):
    """The single pattern group a statement produces, detected on its own."""
    groups = detect_patterns([statement])
    assert len(groups) == 1, f"expected one pattern for {statement!r}"
    return groups[0]


def roles_for(statement: str) -> dict:
    """{group name: (boundary, level)} as inferred for one statement."""
    group = detect_one(statement)
    return {r.group: (r.boundary, r.level) for r in infer_roles(group.named_groups)}


def parse_with(statement: str, roles=None, split: bool = False):
    """Parse `statement` with its own detected pattern."""
    group = detect_one(statement)
    roles = roles if roles is not None else infer_roles(group.named_groups)
    return build_parse_result(
        statement, re.compile(group.regex, re.IGNORECASE), roles, split
    )


def decide(roles, group_name, boundary, level):
    """A cataloguer correcting one row of the role table."""
    for role in roles:
        if role.group == group_name:
            role.boundary, role.level = boundary, level
    return roles


# ---------------------------------------------------------------------------
# Role inference
# ---------------------------------------------------------------------------

def test_plain_range_infers_start_and_end():
    assert roles_for("v.1(1990)-v.5(1994)") == {
        "start_vol":  (BOUNDARY_START, LEVEL_VOL),
        "start_year": (BOUNDARY_START, LEVEL_YEAR),
        "end_vol":    (BOUNDARY_END,   LEVEL_VOL),
        "end_year":   (BOUNDARY_END,   LEVEL_YEAR),
    }


def test_a_compressed_range_names_both_of_its_boundaries():
    """
    "v.1-5(1990-1994)" has no single point dividing a start half from an end
    half: it has two compressed ranges, one per level. Both levels must still
    come out with a start and an end -- the detector once named both years
    end_year, and every value after the volume hyphen landed on the wrong side.
    """
    assert roles_for("v.1-5(1990-1994)") == {
        "start_vol":  (BOUNDARY_START, LEVEL_VOL),
        "end_vol":    (BOUNDARY_END,   LEVEL_VOL),
        "start_year": (BOUNDARY_START, LEVEL_YEAR),
        "end_year":   (BOUNDARY_END,   LEVEL_YEAR),
    }


def test_chronology_only_at_the_end_still_spans_the_range():
    roles = roles_for("v.1:no.1-v.2:no.4(1990-1991)")
    assert roles["start_year"] == (BOUNDARY_START, LEVEL_YEAR)
    assert roles["end_year"]   == (BOUNDARY_END,   LEVEL_YEAR)
    assert roles["start_vol"]  == (BOUNDARY_START, LEVEL_VOL)
    assert roles["end_vol"]    == (BOUNDARY_END,   LEVEL_VOL)


def test_a_number_with_no_caption_is_left_for_the_cataloguer():
    """
    A bare number carries no evidence of its level, and the parser already
    refuses to guess at one. Inference must refuse too, rather than defaulting
    it to a volume and being quietly wrong.

    "v.1(1990)-5(1994)" is the awkward form: a real separator divides it, so the
    "v." does not reach across to the 5, and nothing else says what the 5 is.
    """
    roles = roles_for("v.1(1990)-5(1994)")
    assert roles["start_num"][1] == LEVEL_UNRESOLVED
    assert roles["start_vol"][1] == LEVEL_VOL      # the captioned side is fine


def test_seasons_are_inferred_as_chronology():
    roles = roles_for("v.1:no.1(1990:Spring)-v.5:no.4(1994:Winter)")
    assert roles["start_month"] == (BOUNDARY_START, LEVEL_MONTH)
    assert roles["end_month"]   == (BOUNDARY_END,   LEVEL_MONTH)


def test_a_third_value_at_one_level_is_not_guessed_at():
    """Two boundaries exist; a third value at the same level belongs to neither."""
    roles = infer_roles(["start_year", "end_year", "start_year_3"])
    assert roles[2].level == LEVEL_IGNORE


def test_invented_group_names_are_left_undecided():
    """A cataloguer may edit the expression; an invented name means nothing."""
    roles = {r.group: r.level for r in infer_roles(["foo", "bar_baz", "start_vol"])}
    assert roles["foo"] == LEVEL_UNRESOLVED
    assert roles["bar_baz"] == LEVEL_UNRESOLVED
    assert roles["start_vol"] == LEVEL_VOL


def test_roles_from_regex_reads_the_expression_itself():
    group = detect_one("v.1(1990)-v.5(1994)")
    assert [r.group for r in roles_from_regex(group.regex)] == group.named_groups


def test_editing_the_expression_keeps_decisions_already_made():
    """
    Re-inferring after an edit must not throw away the cataloguer's work: they
    corrected these rows once and should not have to do it again.
    """
    original = infer_roles(["start_vol", "end_num", "end_year"])
    decide(original, "end_num", BOUNDARY_END, LEVEL_VOL)

    merged = {r.group: (r.boundary, r.level)
              for r in merge_roles(["start_vol", "end_num", "end_year", "end_month"],
                                   original)}
    assert merged["end_num"] == (BOUNDARY_END, LEVEL_VOL)      # kept
    assert merged["end_month"][1] == LEVEL_MONTH               # newly inferred


# ---------------------------------------------------------------------------
# Building a ParseResult
# ---------------------------------------------------------------------------

def test_values_land_on_the_right_boundary():
    result = parse_with("v.1(1990)-v.5(1994)")
    assert len(result.ranges) == 1
    hr = result.ranges[0]
    assert (hr.start.vol, hr.start.year) == ("1", "1990")
    assert (hr.end.vol, hr.end.year) == ("5", "1994")


def test_months_are_encoded_as_marc_chronology_codes():
    """
    Borrowed from the parser rather than reimplemented, so a month coded through
    a pattern and the same month coded by the parser cannot drift apart.
    """
    result = parse_with("v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)")
    hr = result.ranges[0]
    assert hr.start.month == "01"
    assert hr.end.month == "12"


def test_seasons_are_encoded_as_marc_season_codes():
    result = parse_with("v.1:no.1(1990:Spring)-v.5:no.4(1994:Winter)")
    hr = result.ranges[0]
    assert hr.start.month == "21"
    assert hr.end.month == "24"


def test_open_ended_statements_stay_open():
    result = parse_with("v.6(1995)-")
    assert result.ranges[0].open_ended is True
    assert result.ranges[0].end is None


def test_a_corrected_role_changes_the_parsed_result():
    """The whole point of the confirmation screen, at the data level."""
    group = detect_one("v.1(1990)-5(1994)")
    roles = infer_roles(group.named_groups)
    decide(roles, "start_num", BOUNDARY_END, LEVEL_VOL)

    hr = build_parse_result(
        "v.1(1990)-5(1994)", re.compile(group.regex, re.IGNORECASE), roles, False
    ).ranges[0]

    assert (hr.start.vol, hr.start.year) == ("1", "1990")
    assert (hr.end.vol, hr.end.year) == ("5", "1994")


def test_a_value_set_to_not_encoded_is_reported_rather_than_dropped():
    group = detect_one("v.1(1990)-5(1994)")
    roles = decide(infer_roles(group.named_groups), "start_num",
                   BOUNDARY_START, LEVEL_IGNORE)

    result = build_parse_result(
        "v.1(1990)-5(1994)", re.compile(group.regex, re.IGNORECASE), roles, False
    )
    assert any("'5'" in w for w in result.warnings)


def test_multi_range_statements_become_several_ranges():
    statement = "v.1(1990)-v.3(1992), v.5(1994)-"
    group = detect_patterns(["v.1(1990)-v.3(1992)"])[0]

    result = build_parse_result(
        statement, re.compile(group.regex, re.IGNORECASE),
        infer_roles(group.named_groups), split=True,
    )
    assert len(result.ranges) == 2
    assert result.ranges[0].start.vol == "1"
    assert result.ranges[1].start.vol == "5"
    assert result.ranges[1].open_ended is True


def test_a_segment_the_pattern_misses_falls_to_the_parser_not_the_floor():
    """
    A pattern covering most of a statement must not cost the rest of it.

    The second range here has an issue caption the pattern knows nothing about,
    so the parser reads it and both ranges survive.
    """
    group = detect_patterns(["v.1(1990)-v.3(1992)"])[0]
    statement = "v.1(1990)-v.3(1992), v.5:no.2(1994)-v.6:no.4(1995)"

    result = build_parse_result(
        statement, re.compile(group.regex, re.IGNORECASE),
        infer_roles(group.named_groups), split=True,
    )
    assert len(result.ranges) == 2
    assert result.ranges[1].start.issue == "2"
    assert any("standard parser" in w for w in result.warnings)


def test_a_statement_the_pattern_cannot_match_returns_nothing():
    """None is the signal to fall back; anything else would strand the caller."""
    group = detect_one("v.1(1990)-v.5(1994)")
    assert build_parse_result(
        "1993: (1 [Feb])", re.compile(group.regex, re.IGNORECASE),
        infer_roles(group.named_groups), False
    ) is None


# ---------------------------------------------------------------------------
# Applying a library
# ---------------------------------------------------------------------------

def confirmed(statement: str, decisions=(), split=False, priority=0):
    """A ConfirmedPattern built from one statement, with corrections applied."""
    group = detect_one(statement)
    roles = infer_roles(group.named_groups)
    for name, boundary, level in decisions:
        decide(roles, name, boundary, level)
    pattern, errors = plib.validate_pattern({
        "label": group.human_label,
        "regex": group.regex,
        "roles": [r.to_dict() for r in roles],
        "split": split,
        "priority": priority,
    })
    assert not errors, errors
    return pattern


def test_the_parser_still_handles_everything_no_pattern_matches():
    """The guarantee the whole design rests on."""
    pattern = confirmed("v.1(1990)-v.5(1994)")
    result, source = apply_patterns("1993: (1 [Feb])", [pattern])
    assert source == "parser"
    assert result.ranges[0].start.year == "1993"


def test_an_empty_library_is_exactly_the_parser():
    for statement in ("v.1(1990)-v.5(1994)", "1993: (1 [Feb])", "2016?", "?: 16"):
        via_library, source = apply_patterns(statement, [])
        direct = parse_866(statement)
        assert source == "parser"
        assert [str(r.start) for r in via_library.ranges] == \
               [str(r.start) for r in direct.ranges]


def test_a_confirmed_pattern_is_preferred_to_the_parser():
    pattern = confirmed("v.1(1990)-v.5(1994)")
    _, source = apply_patterns("v.2(1991)-v.4(1993)", [pattern])
    assert source == pattern.id


def test_a_pattern_can_convert_what_the_parser_holds_for_review():
    """
    "?: 16" is a number with nothing to say whether it is a volume or an issue,
    so the parser holds it back rather than guessing. A cataloguer who knows the
    collection can say, and then it converts.
    """
    assert parse_866("?: 16").needs_review is True

    group = detect_one("?: 16")
    number = next(g for g in group.named_groups if g.endswith("num"))
    pattern = confirmed("?: 16", decisions=[(number, BOUNDARY_START, LEVEL_ISSUE)])

    result, source = apply_patterns("?: 16", [pattern])
    assert source == pattern.id
    assert result.needs_review is False
    assert result.ranges[0].start.issue == "16"


def test_higher_priority_patterns_are_tried_first():
    broad = confirmed("v.1(1990)-v.5(1994)", priority=1)
    narrow = confirmed("v.1(1990)-v.5(1994)", priority=9)
    narrow.id = "narrow"
    ordered = plib.order_patterns([broad, narrow])
    _, source = apply_patterns("v.2(1991)-v.4(1993)", ordered)
    assert source == "narrow"


# ---------------------------------------------------------------------------
# The library: what it refuses to store
# ---------------------------------------------------------------------------

def test_two_roles_cannot_claim_one_subfield():
    group = detect_one("v.1(1990)-v.5(1994)")
    _, errors = plib.validate_pattern({
        "label": "clash",
        "regex": group.regex,
        "roles": [
            {"group": "start_vol",  "boundary": "start", "level": "vol"},
            {"group": "start_year", "boundary": "start", "level": "vol"},
            {"group": "end_vol",    "boundary": "end",   "level": "vol"},
            {"group": "end_year",   "boundary": "end",   "level": "year"},
        ],
    })
    assert any("only one value can go" in e for e in errors)


def test_every_captured_value_needs_a_decision():
    group = detect_one("v.1(1990)-v.5(1994)")
    _, errors = plib.validate_pattern({
        "label": "partial", "regex": group.regex,
        "roles": [{"group": "start_vol", "boundary": "start", "level": "vol"}],
    })
    assert any("no role decided" in e for e in errors)


def test_a_role_naming_a_group_that_does_not_exist_is_rejected():
    group = detect_one("v.1(1990)-v.5(1994)")
    _, errors = plib.validate_pattern({
        "label": "ghost", "regex": group.regex,
        "roles": [{"group": "nonexistent", "boundary": "start", "level": "vol"}],
    })
    assert any("does not capture" in e for e in errors)


def test_an_unknown_level_is_rejected():
    group = detect_one("v.1(1990)-v.5(1994)")
    _, errors = plib.validate_pattern({
        "label": "odd", "regex": group.regex,
        "roles": [{"group": "start_vol", "boundary": "start", "level": "colour"}],
    })
    assert any("unknown level" in e for e in errors)


def test_an_expression_too_long_to_test_is_rejected():
    """
    The detector's Test button refuses anything longer, so a pattern over the
    cap could never be checked against real statements before being trusted.
    """
    _, errors = plib.validate_pattern({
        "label": "huge",
        "regex": "(?P<start_vol>a)" + "b" * plib.MAX_REGEX_CHARS,
        "roles": [{"group": "start_vol", "boundary": "start", "level": "vol"}],
    })
    assert any("limit" in e for e in errors)


def test_an_unreadable_expression_is_rejected():
    _, errors = plib.validate_pattern({
        "label": "broken", "regex": "(?P<start_vol>[", "roles": []})
    assert any("could not be read" in e for e in errors)


def test_a_pattern_that_encodes_nothing_is_rejected():
    group = detect_one("v.1(1990)-v.5(1994)")
    _, errors = plib.validate_pattern({
        "label": "empty", "regex": group.regex,
        "roles": [{"group": g, "boundary": "start", "level": LEVEL_IGNORE}
                  for g in group.named_groups],
    })
    assert any("no holdings" in e for e in errors)


def test_a_library_survives_export_and_import():
    patterns = [confirmed("v.1(1990)-v.5(1994)", priority=3)]
    restored, errors = plib.from_export(plib.to_export(patterns))
    assert not errors
    assert [p.to_dict() for p in restored] == [p.to_dict() for p in patterns]


def test_a_library_from_a_future_format_is_refused_rather_than_misread():
    _, errors = plib.from_export({"schema": 99, "patterns": []})
    assert any("version" in e for e in errors)
