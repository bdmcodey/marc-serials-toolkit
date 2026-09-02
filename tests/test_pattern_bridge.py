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
    GroupRole,
    BOUNDARY_START,
    KIND_ENUM,
    KIND_IGNORE,
    KIND_MONTH,
    KIND_UNRESOLVED,
    KIND_YEAR,
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
    """
    {group name: (boundary, kind)} as inferred for one statement.

    Enumeration reports its level and caption too, since those are the whole
    point of the positional model: (boundary, "enum", level, caption).
    """
    group = detect_one(statement)
    out = {}
    for r in infer_roles(group.named_groups):
        out[r.group] = ((r.boundary, r.kind, r.level, r.caption)
                        if r.kind == KIND_ENUM else (r.boundary, r.kind))
    return out


def parse_with(statement: str, roles=None, split: bool = False):
    """Parse `statement` with its own detected pattern."""
    group = detect_one(statement)
    roles = roles if roles is not None else infer_roles(group.named_groups)
    return build_parse_result(
        statement, re.compile(group.regex, re.IGNORECASE), roles, split
    )


def decide(roles, group_name, boundary, kind, level=None, caption=None):
    """A cataloguer correcting one row of the role table."""
    for role in roles:
        if role.group == group_name:
            role.boundary, role.kind = boundary, kind
            if kind == KIND_ENUM:
                role.level, role.caption = level, caption
    return roles


# ---------------------------------------------------------------------------
# Role inference
# ---------------------------------------------------------------------------

def test_plain_range_infers_start_and_end():
    assert roles_for("v.1(1990)-v.5(1994)") == {
        "start_vol":  (BOUNDARY_START, KIND_ENUM, 0, "v."),
        "start_year": (BOUNDARY_START, KIND_YEAR),
        "end_vol":    (BOUNDARY_END,   KIND_ENUM, 0, "v."),
        "end_year":   (BOUNDARY_END,   KIND_YEAR),
    }


def test_a_compressed_range_names_both_of_its_boundaries():
    """
    "v.1-5(1990-1994)" has no single point dividing a start half from an end
    half: it has two compressed ranges, one per level. Both levels must still
    come out with a start and an end -- the detector once named both years
    end_year, and every value after the volume hyphen landed on the wrong side.
    """
    assert roles_for("v.1-5(1990-1994)") == {
        "start_vol":  (BOUNDARY_START, KIND_ENUM, 0, "v."),
        "end_vol":    (BOUNDARY_END,   KIND_ENUM, 0, "v."),
        "start_year": (BOUNDARY_START, KIND_YEAR),
        "end_year":   (BOUNDARY_END,   KIND_YEAR),
    }


def test_chronology_only_at_the_end_still_spans_the_range():
    roles = roles_for("v.1:no.1-v.2:no.4(1990-1991)")
    assert roles["start_year"] == (BOUNDARY_START, KIND_YEAR)
    assert roles["end_year"]   == (BOUNDARY_END,   KIND_YEAR)
    assert roles["start_vol"]  == (BOUNDARY_START, KIND_ENUM, 0, "v.")
    assert roles["end_vol"]    == (BOUNDARY_END,   KIND_ENUM, 0, "v.")
    assert roles["start_iss"]  == (BOUNDARY_START, KIND_ENUM, 1, "no.")


def test_a_number_with_no_caption_is_left_for_the_cataloguer():
    """
    A bare number carries no evidence of its level, and the parser already
    refuses to guess at one. Inference must refuse too, rather than defaulting
    it to a volume and being quietly wrong.

    "v.1(1990)-5(1994)" is the awkward form: a real separator divides it, so the
    "v." does not reach across to the 5, and nothing else says what the 5 is.
    """
    roles = roles_for("v.1(1990)-5(1994)")
    assert roles["start_num"][1] == KIND_UNRESOLVED
    assert roles["start_vol"][1] == KIND_ENUM      # the captioned side is fine


def test_seasons_are_inferred_as_chronology():
    roles = roles_for("v.1:no.1(1990:Spring)-v.5:no.4(1994:Winter)")
    assert roles["start_month"] == (BOUNDARY_START, KIND_MONTH)
    assert roles["end_month"]   == (BOUNDARY_END,   KIND_MONTH)


def test_a_third_value_at_one_level_is_not_guessed_at():
    """Two boundaries exist; a third value at the same level belongs to neither."""
    roles = infer_roles(["start_year", "end_year", "start_year_3"])
    assert roles[2].kind == KIND_IGNORE


def test_invented_group_names_are_left_undecided():
    """A cataloguer may edit the expression; an invented name means nothing."""
    roles = {r.group: r.kind for r in infer_roles(["foo", "bar_baz", "start_vol"])}
    assert roles["foo"] == KIND_UNRESOLVED
    assert roles["bar_baz"] == KIND_UNRESOLVED
    assert roles["start_vol"] == KIND_ENUM


def test_roles_from_regex_reads_the_expression_itself():
    group = detect_one("v.1(1990)-v.5(1994)")
    assert [r.group for r in roles_from_regex(group.regex)] == group.named_groups


def test_editing_the_expression_keeps_decisions_already_made():
    """
    Re-inferring after an edit must not throw away the cataloguer's work: they
    corrected these rows once and should not have to do it again.
    """
    original = infer_roles(["start_vol", "end_num", "end_year"])
    decide(original, "end_num", BOUNDARY_END, KIND_ENUM, level=0, caption="v.")

    merged = {r.group: (r.boundary, r.kind, r.level, r.caption)
              for r in merge_roles(["start_vol", "end_num", "end_year", "end_month"],
                                   original)}
    assert merged["end_num"] == (BOUNDARY_END, KIND_ENUM, 0, "v.")   # kept
    assert merged["end_month"][1] == KIND_MONTH                      # newly inferred


# ---------------------------------------------------------------------------
# Building a ParseResult
# ---------------------------------------------------------------------------

def test_values_land_on_the_right_boundary():
    result = parse_with("v.1(1990)-v.5(1994)")
    assert len(result.ranges) == 1
    hr = result.ranges[0]
    assert (hr.start.value_at(0), hr.start.year) == ("1", "1990")
    assert (hr.end.value_at(0), hr.end.year) == ("5", "1994")


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
    decide(roles, "start_num", BOUNDARY_END, KIND_ENUM)

    hr = build_parse_result(
        "v.1(1990)-5(1994)", re.compile(group.regex, re.IGNORECASE), roles, False
    ).ranges[0]

    assert (hr.start.value_at(0), hr.start.year) == ("1", "1990")
    assert (hr.end.value_at(0), hr.end.year) == ("5", "1994")


def test_a_value_set_to_not_encoded_is_reported_rather_than_dropped():
    group = detect_one("v.1(1990)-5(1994)")
    roles = decide(infer_roles(group.named_groups), "start_num",
                   BOUNDARY_START, KIND_IGNORE)

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
    assert result.ranges[0].start.value_at(0) == "1"
    assert result.ranges[1].start.value_at(0) == "5"
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
    assert result.ranges[1].start.value_at(1) == "2"
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
    pattern = confirmed("?: 16", decisions=[(number, BOUNDARY_START, KIND_ENUM)])

    result, source = apply_patterns("?: 16", [pattern])
    assert source == pattern.id
    assert result.needs_review is False
    # Nothing said which level it is, so it is the first one the record has.
    assert result.ranges[0].start.value_at(0) == "16"


def test_higher_priority_patterns_are_tried_first():
    broad = confirmed("v.1(1990)-v.5(1994)", priority=1)
    narrow = confirmed("v.1(1990)-v.5(1994)", priority=9)
    narrow.id = "narrow"
    ordered = plib.order_patterns([broad, narrow])
    _, source = apply_patterns("v.2(1991)-v.4(1993)", ordered)
    assert source == "narrow"


def test_without_the_parser_an_unmatched_statement_is_left_alone():
    """
    Switched off, only confirmed patterns convert. A statement none of them
    matches produces nothing, so its 866 survives untouched.
    """
    pattern = confirmed("v.1(1990)-v.5(1994)")
    result, source = apply_patterns("1993: (1 [Feb])", [pattern], fallback=False)

    assert source == "unmatched"
    assert result.ranges == []
    assert any("left as it is" in w for w in result.warnings)


def test_without_the_parser_a_half_matching_statement_converts_nothing():
    """
    The case that decides the rule. The 866 is removed once anything is written
    from it, so converting the first range of a two-range statement and dropping
    the second would delete holdings outright -- worse than converting nothing.

    With the parser available the second range is recovered, so this only bites
    when it is switched off. All or nothing keeps the field intact.
    """
    group = detect_one("v.1(1990)-v.3(1992)")
    pattern = confirmed("v.1(1990)-v.3(1992)", split=True)
    statement = "v.1(1990)-v.3(1992), v.5:no.2(1994)-v.6:no.4(1995)"

    with_parser = build_parse_result(
        statement, pattern.compiled(), pattern.roles, split=True, fallback=True)
    assert len(with_parser.ranges) == 2          # second range recovered

    without = build_parse_result(
        statement, pattern.compiled(), pattern.roles, split=True, fallback=False)
    assert without is None                       # nothing written, 866 survives


def test_the_parser_still_reads_unmatched_statements_by_default():
    """The switch defaults to on, so nothing changes for anyone not using it."""
    pattern = confirmed("v.1(1990)-v.5(1994)")
    _, source = apply_patterns("1993: (1 [Feb])", [pattern])
    assert source == "parser"


def test_a_matching_statement_converts_either_way():
    """Turning the parser off must not disturb what the patterns themselves do."""
    pattern = confirmed("v.1(1990)-v.5(1994)")
    on, src_on = apply_patterns("v.2(1991)-v.4(1993)", [pattern], fallback=True)
    off, src_off = apply_patterns("v.2(1991)-v.4(1993)", [pattern], fallback=False)
    assert src_on == src_off == pattern.id
    assert [str(r.start) for r in on.ranges] == [str(r.start) for r in off.ranges]


# ---------------------------------------------------------------------------
# The library: what it refuses to store
# ---------------------------------------------------------------------------

def test_two_roles_cannot_claim_one_subfield():
    """
    Two values set to the same enumeration level of the same boundary would
    write to one subfield, and the second would quietly win.
    """
    group = detect_one("v.1(1990)-v.5(1994)")
    _, errors = plib.validate_pattern({
        "label": "clash",
        "regex": group.regex,
        "roles": [
            {"group": "start_vol",  "boundary": "start", "kind": "enum", "level": 0},
            {"group": "start_year", "boundary": "start", "kind": "enum", "level": 0},
            {"group": "end_vol",    "boundary": "end",   "kind": "enum", "level": 0},
            {"group": "end_year",   "boundary": "end",   "kind": "year"},
        ],
    })
    assert any("only one value can go" in e for e in errors)


def test_two_unnumbered_enumeration_levels_are_not_a_clash():
    """
    A screen that leaves the level to position sends none at all. Two of those
    are the first and second level, not two claims on the first -- reading them
    as a clash would make the ordinary case unsavable.
    """
    group = detect_one("v.1:no.1-v.2:no.4(1990-1991)")
    pattern, errors = plib.validate_pattern({
        "label": "positional",
        "regex": group.regex,
        "roles": [{"group": g, "boundary": b, "kind": k, "caption": c}
                  for g, b, k, c in [
                      ("start_vol",  "start", "enum",  "v."),
                      ("start_iss",  "start", "enum",  "no."),
                      ("end_vol",    "end",   "enum",  "v."),
                      ("end_iss",    "end",   "enum",  "no."),
                      ("start_year", "start", "year",  None),
                      ("end_year",   "end",   "year",  None),
                  ]],
    })
    assert not errors, errors
    levels = {r.group: r.level for r in pattern.roles}
    assert levels["start_vol"] == 0 and levels["start_iss"] == 1
    assert levels["end_vol"] == 0 and levels["end_iss"] == 1


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


def test_a_role_that_names_nothing_readable_is_rejected():
    """
    A stored library is read back with no way to ask what it meant, so a role
    naming a level this version does not know is refused rather than guessed
    at. It arrives as undecided, which is the one state a stored pattern may
    not be in.
    """
    group = detect_one("v.1(1990)-v.5(1994)")
    _, errors = plib.validate_pattern({
        "label": "odd", "regex": group.regex,
        "roles": [{"group": "start_vol", "boundary": "start", "level": "colour"}],
    })
    assert any("no level decided" in e for e in errors)


def test_an_unknown_kind_is_rejected():
    group = detect_one("v.1(1990)-v.5(1994)")
    _, errors = plib.validate_pattern({
        "label": "odd", "regex": group.regex,
        "roles": [{"group": "start_vol", "boundary": "start", "kind": "colour"}],
    })
    assert any("unknown kind" in e for e in errors)


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
        "roles": [{"group": g, "boundary": "start", "level": KIND_IGNORE}
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


# ---------------------------------------------------------------------------
# Levels read from convention rather than from the statement
# ---------------------------------------------------------------------------

def test_a_captionless_number_above_an_issue_is_suggested_as_a_volume():
    """
    "39 no 1" is v.39 no.1 -- a number sitting a level above an issue is a
    volume, and holdings_parser reads the same statement the same way. It is a
    convention rather than something the statement states, so it arrives as a
    default that still has to be accepted.
    """
    group = detect_one("39 no 1 (Spring 1995)")
    roles = {r.group: r for r in infer_roles(group.named_groups)}

    assert roles["start_num"].kind == KIND_ENUM
    assert roles["start_num"].caption == "v."
    assert roles["start_num"].suggested is True
    assert roles["start_num"].needs_a_decision is True
    # The captioned levels are stated outright and need no confirming.
    assert roles["start_iss"].suggested is False


def test_a_captionless_number_with_no_issue_after_it_is_not_suggested():
    """Nothing to sit above means nothing to infer from."""
    for statement in ("4 (Summer, Autumn 1990)", "?: 16"):
        roles = {r.group: r for r in infer_roles(detect_one(statement).named_groups)}
        assert roles["start_num"].kind == KIND_UNRESOLVED, statement
        assert roles["start_num"].suggested is False, statement


def test_an_ordinary_pattern_suggests_nothing():
    """Captions state the level, so nothing about them is a guess."""
    roles = infer_roles(detect_one("v.1(1990)-v.5(1994)").named_groups)
    assert not any(r.suggested for r in roles)
    assert not any(r.needs_a_decision for r in roles)


def test_a_suggested_role_survives_the_round_trip():
    role = GroupRole("start_num", BOUNDARY_START, KIND_ENUM, suggested=True)
    assert GroupRole.from_dict(role.to_dict()).suggested is True


# ---------------------------------------------------------------------------
# A pattern must span the statement it claims
# ---------------------------------------------------------------------------

def test_a_pattern_matching_only_part_of_a_statement_is_not_used():
    """
    A confirmed pattern must span the whole segment, or it does not apply.

    build_parse_result() matched with `fullmatch(seg) or search(seg)` until
    September 2026, so a pattern for the very common single-unit shape
    "v. 9 no. 1 (Nov 1902)" would search-match the *tail* of a two-boundary
    statement and convert on it. Everything before the matched span -- here the
    entire first boundary, v. 1 no. 1 (1995) -- was discarded with no warning,
    and the Converter removes the 866 once anything has been written from it.

    The shortest pattern is the one a cataloguer confirms first, because it has
    the largest cluster behind it, so this was reachable on an ordinary run
    rather than a contrived one.
    """
    donor = detect_one("v. 9 no. 1 (Nov 1902)")
    compiled = re.compile(donor.regex, re.IGNORECASE)
    roles = infer_roles(donor.named_groups)

    victim = "v. 1 no. 1 (1995)-v. 12 no. 4 (December 2006)"
    assert compiled.fullmatch(victim) is None
    assert compiled.search(victim) is not None, "the tail still matches"

    # Without the standard parser to fall back on, nothing is written at all.
    assert build_parse_result(victim, compiled, roles, split=False,
                              fallback=False) is None


def test_a_partly_matching_pattern_falls_back_to_the_parser_intact():
    """
    With the fallback on, the statement is read by parse_866() -- whole.

    The pattern contributed nothing, so what reaches the converter has to be
    what the Converter itself would have produced, not the tail the pattern
    could see.
    """
    donor = detect_one("v. 9 no. 1 (Nov 1902)")
    compiled = re.compile(donor.regex, re.IGNORECASE)
    roles = infer_roles(donor.named_groups)

    victim = "v. 1 no. 1 (1995)-v. 12 no. 4 (December 2006)"
    result = build_parse_result(victim, compiled, roles, split=False, fallback=True)

    assert result is None, "no segment matched, so the caller falls back wholesale"

    # And through the public entry point, with the pattern in the library.
    pattern = plib.ConfirmedPattern(
        id="p1", label=donor.human_label, regex=donor.regex, roles=roles,
    )
    parsed, source = apply_patterns(victim, [pattern])
    assert source == "parser"
    assert parsed.ranges[0].start.value_at(0) == "1"      # the first boundary survives
    assert parsed.ranges[0].start.value_at(1) == "1"
    assert parsed.ranges[0].start.year == "1995"
    assert parsed.ranges[0].end.value_at(0) == "12"


def test_a_value_nobody_has_decided_about_forces_review():
    """
    An undecided role is not the same as one set to 'Not encoded'. The second
    is a cataloguer's choice; the first is a value that reached the screen and
    was never accounted for, which is the one thing that must not pass quietly.
    """
    group = detect_one("Series 1, v. 6 no. 1 (Summer/Fall 1992)")
    roles = infer_roles(group.named_groups)
    assert any(r.kind == KIND_UNRESOLVED for r in roles)

    result = parse_with("Series 1, v. 6 no. 1 (Summer/Fall 1992)", roles=roles)
    assert result.needs_review is True
    assert any("not encoded" in w for w in result.warnings)


def test_a_value_deliberately_left_out_does_not_force_review():
    """The complement: 'Not encoded' is an answer, so it settles the row."""
    group = detect_one("Series 1, v. 6 no. 1 (Summer/Fall 1992)")
    roles = infer_roles(group.named_groups)
    for role in roles:
        if role.kind == KIND_UNRESOLVED:
            role.kind = KIND_IGNORE

    result = parse_with("Series 1, v. 6 no. 1 (Summer/Fall 1992)", roles=roles)
    assert result.needs_review is False
