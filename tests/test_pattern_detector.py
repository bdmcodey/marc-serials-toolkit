"""
Tests for pattern_detector: tokenising, clustering, and regex generation.

split_multi_range() gets a disproportionate share of this file because the risk
there is silent corruption rather than a visible failure. A slash is meaningful
inside a holdings statement -- v.1/2 is a combined volume, 1990/91 a split year,
Jan./Feb. a combined month -- so a separator rule that is even slightly too eager
would quietly cut real statements in half. The discrimination table below is the
regression test for the fix released in 0.5.1.
"""

from __future__ import annotations

import re

import pytest

from pattern_detector import (detect_patterns, split_multi_range, get_signature,
                              tokenize, MAX_PATTERN_TOKENS)


# ---------------------------------------------------------------------------
# Tokenising
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, kind", [
    ("1990", "YEAR"),        # YEAR must beat NUMBER
    ("Nov.", "CHRON"),       # CHRON must beat ISS_CAP, or "Nov." becomes "no" + junk
    ("no.", "ISS_CAP"),
    ("v.", "VOL_CAP"),
    ("Volume", "VOL_CAP"),
    ("pt.", "PT_CAP"),
    ("Spring", "CHRON"),     # months and seasons share one kind
    ("4a", "NUMBER"),        # a trailing letter stays part of one number
])
def test_token_kinds(text, kind):
    """
    Pattern order in the tokeniser is load-bearing: first match wins per
    position, so these are the cases where a reordering would silently change
    what the detector thinks it is looking at.
    """
    tokens = tokenize(text)
    assert tokens[0].kind == kind


@pytest.mark.parametrize("text, kind", [
    ("1799", "NUMBER"), ("1800", "YEAR"),
    ("2099", "YEAR"), ("2100", "NUMBER"),
])
def test_year_recognition_boundaries(text, kind):
    assert tokenize(text)[0].kind == kind


def test_springtime_is_not_a_season():
    """The season pattern is word-bounded; "springtime" is ordinary text."""
    assert tokenize("springtime")[0].kind != "CHRON"


@pytest.mark.parametrize("text", ["Jul/Aug", "Winter/Spring", "Jan/Feb/Mar",
                                  "November/December"])
def test_a_combined_chronology_is_one_token(text):
    """
    "Jul/Aug" is one value written with a slash, not two values with noise
    between them. Tokenising it as three tokens put "(Jul/Aug 2017)" and
    "(Apr 2019)" in different clusters, and left the slash -- which always means
    something in holdings -- as UNKNOWN, so generated regexes carried a lazy
    wildcard where a literal "/" belonged.
    """
    tokens = tokenize(text)
    assert len(tokens) == 1
    assert tokens[0].kind == "CHRON"
    assert tokens[0].raw == text


# ---------------------------------------------------------------------------
# Fuzzy signatures
# ---------------------------------------------------------------------------

def test_caption_variants_share_a_signature():
    """
    The whole clustering premise in one assertion: "v.1(1990)" and
    "Vol. 2 (1991)" differ in style, not in structure, so they must land in the
    same group and be described by one regex.
    """
    assert get_signature("v.1(1990)") == get_signature("Vol. 2 (1991)")


def test_different_structures_get_different_signatures():
    assert get_signature("v.1(1990)") != get_signature("v.1:no.1(1990)")


def test_free_text_length_does_not_split_a_signature():
    """
    Consecutive unknown tokens collapse, so two statements differing only in the
    length of a cataloguer's note still cluster together instead of each
    becoming a bespoke one-off pattern.

    Note the notes here carry no punctuation the tokeniser recognises. A colon
    in "Library has:" is a SEP_COLON, not free text, and legitimately changes
    the structure -- collapsing applies to unknown runs, not to punctuation.
    """
    assert get_signature("Library has v.1(1990)") == get_signature("[lacks] v.1(1990)")
    assert get_signature("incomplete run v.1(1990)") == get_signature("[lacks] v.1(1990)")


# ---------------------------------------------------------------------------
# split_multi_range -- what separates, and what must not
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    # Separators at paren depth 0 split.
    ("v.1(1990)-v.3(1992), v.5(1994)-", ["v.1(1990)-v.3(1992)", "v.5(1994)-"]),
    ("v.1(1990); v.2(1991)",            ["v.1(1990)", "v.2(1991)"]),
    ("v.1(1990) / v.2(1991)",           ["v.1(1990)", "v.2(1991)"]),
    ("v.1(1990)-v.3(1992), v.5(1994)- / v.9(1998)-",
     ["v.1(1990)-v.3(1992)", "v.5(1994)-", "v.9(1998)-"]),
])
def test_statements_split_on_top_level_separators(text, expected):
    assert split_multi_range(text) == expected


@pytest.mark.parametrize("text", [
    "v.1/2(1990)",                  # combined volume
    "1990/91",                      # split year
    "v.5:no.1/2(1994:Jan./Feb.)",   # combined issue and combined month
    "v.1(1990/91)-v.3(1992/93)",    # split years either side of a range
])
def test_meaningful_slashes_are_never_split(text):
    """
    A slash only separates when whitespace surrounds it. Splitting any of these
    would cut a single value in half and change what the holdings say.
    """
    assert split_multi_range(text) == [text]


def test_separators_inside_parentheses_do_not_split():
    assert split_multi_range("v.1(1990, 1991)") == ["v.1(1990, 1991)"]


@pytest.mark.parametrize("text", ["/", "", "   ", ",,,", "v.1(1990) /", "/ v.1(1990)"])
def test_degenerate_input_does_not_crash(text):
    """
    Never raise and never return an empty list: the caller feeds the result
    straight into clustering, so a missing fallback would lose the statement.
    """
    parts = split_multi_range(text)
    assert isinstance(parts, list)
    assert parts != []


# ---------------------------------------------------------------------------
# Clustering and regex generation
# ---------------------------------------------------------------------------

def test_no_statements_means_no_groups():
    assert detect_patterns([]) == []


def test_blank_statements_are_dropped():
    assert detect_patterns(["", "   "]) == []


def test_homogeneous_cluster_matches_every_member():
    groups = detect_patterns(["v.1(1990)-v.3(1992)",
                              "v.5(1994)-v.8(1997)",
                              "v.10(1999)-v.14(2003)"])
    assert len(groups) == 1
    assert groups[0].count == 3
    assert groups[0].match_rate == 1.0
    assert groups[0].failed == []


def test_every_statement_lands_in_exactly_one_group():
    statements = ["v.1(1990)", "Vol. 2 (1991)", "v.1:no.1(1990:Jan.)", "1993: (1 [Feb])"]
    groups = detect_patterns(statements)
    assert sum(g.count for g in groups) == len(statements)


def test_generated_regexes_compile_and_declare_their_groups():
    groups = detect_patterns(["v.1(1990)-v.3(1992)", "v.5(1994)-v.8(1997)"])
    for group in groups:
        compiled = re.compile(group.regex, re.IGNORECASE)
        assert set(group.named_groups) == set(compiled.groupindex)


def test_a_compressed_range_is_not_a_unit_separator():
    """
    The hyphen in "v.1-5" joins two volumes; it does not divide the statement
    into a start unit and an end unit. Treating it as though it did put every
    later value on the far side of a range that was not there -- both years in
    "v.1-5(1990-1994)" came out named end_year.

    holdings_parser._smart_split_range has always drawn this distinction; this
    is the same rule in token form.
    """
    groups = detect_patterns(["v.1-5(1990-1994)"])[0]
    assert groups.named_groups == ["start_vol", "end_vol", "start_year", "end_year"]


def test_a_real_unit_separator_is_still_found():
    """The distinction must not cost the ordinary shape its separator."""
    assert detect_patterns(["v.1(1990)-v.5(1994)"])[0].named_groups == \
        ["start_vol", "start_year", "end_vol", "end_year"]
    assert detect_patterns(["1990-1994"])[0].named_groups == \
        ["start_year", "end_year"]


def test_each_level_gets_at_most_one_start_and_one_end():
    """
    Which boundary a value sits on is a property of its own level, not of the
    statement. Every level should therefore name at most a start and an end,
    whatever shape the statement takes.
    """
    for statement in ("v.1-5(1990-1994)", "v.1(1990)-v.5(1994)",
                      "v.1:no.1-v.2:no.4(1990-1991)",
                      "v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)"):
        names = detect_patterns([statement])[0].named_groups
        assert len(names) == len(set(names)), statement
        for level in ("vol", "iss", "year", "month"):
            at_level = [n for n in names if n.endswith(f"_{level}")]
            assert len(at_level) <= 2, (statement, at_level)
            assert len(at_level) == len(set(at_level)), (statement, at_level)


def test_a_captionless_value_across_a_separator_is_still_just_a_number():
    """
    A caption does not reach across a unit separator: the "v." in
    "v.1(1990)-5(1994)" says nothing about the 5, so it stays a bare number for
    a cataloguer to place rather than being claimed as a volume.
    """
    assert "start_num" in detect_patterns(["v.1(1990)-5(1994)"])[0].named_groups


def test_a_compressed_range_reads_as_one_in_its_label():
    """The label is how a cataloguer picks a pattern out of a list of them."""
    assert detect_patterns(["v.1-5(1990-1994)"])[0].human_label == "VOL-VOL(YEAR-YEAR)"


def test_caption_variants_are_recorded_and_matched():
    """A group spanning "v." and "Vol." must match both, not just the first."""
    groups = detect_patterns(["v.1(1990)", "Vol. 2 (1991)"])
    assert len(groups) == 1
    assert groups[0].match_rate == 1.0
    assert len(groups[0].caption_variants.get("vol", [])) >= 2


def test_groups_are_ordered_by_size():
    groups = detect_patterns(["v.1(1990)", "v.2(1991)", "v.3(1992)",
                              "v.1:no.1(1990:Jan.)"])
    assert [g.count for g in groups] == sorted((g.count for g in groups), reverse=True)


def test_regex_generation_is_deterministic():
    """
    The builder walks dicts and uses a set for name disambiguation, so an
    ordering bug here would show up as a regex that changes between runs.
    """
    statements = ["v.1(1990)-v.3(1992)", "Vol. 5 (1994)-Vol. 8 (1997)"]
    assert [g.regex for g in detect_patterns(statements)] == \
           [g.regex for g in detect_patterns(statements)]


def test_to_dict_is_json_safe():
    import json
    groups = detect_patterns(["v.1(1990)-v.3(1992)"])
    json.dumps(groups[0].to_dict())      # raises if anything is not serialisable


# ---------------------------------------------------------------------------
# The complexity guard
# ---------------------------------------------------------------------------

LONG_RUN_ON = ("1977: (46[Jul], 48-51[Sep-Dec])"
               "1978: (52-60[Jan-Jun], 61-70[Jul-Dec])"
               "1979: (71-80[Jan-Jun], 81-90[Jul-Dec])")


def test_over_long_cluster_is_reported_not_converted():
    """
    Past a certain length the generated regex is one nobody can read and the
    tool's own Test button would reject it, so the detector reports a finding
    instead. The statement must survive in `examples` -- a finding the cataloguer
    cannot see is worse than no finding at all.
    """
    group = detect_patterns([LONG_RUN_ON])[0]

    assert group.too_complex is True
    assert group.token_count > MAX_PATTERN_TOKENS
    assert group.regex == ""
    assert group.named_groups == []
    assert group.examples == [LONG_RUN_ON]
    assert group.human_label


def test_ordinary_statements_stay_under_the_guard():
    for group in detect_patterns(["v.1(1990)-v.3(1992)", "1993: (1 [Feb])"]):
        assert group.too_complex is False
        assert group.token_count <= MAX_PATTERN_TOKENS
        assert group.regex


def test_too_complex_implies_no_regex_and_vice_versa():
    """The guard and the output must never disagree about what was emitted."""
    for group in detect_patterns(["v.1(1990)-v.3(1992)", LONG_RUN_ON]):
        assert group.too_complex == (group.regex == "")
        assert group.too_complex == (group.token_count > MAX_PATTERN_TOKENS)


# ---------------------------------------------------------------------------
# One shape, one cluster
# ---------------------------------------------------------------------------

def test_months_and_seasons_cluster_together():
    """
    "(Sep 1944 - Aug 1945)" and "(Winter 1986 - Summer 1987)" are one shape to a
    cataloguer, and were two clusters -- two confirmations for the same
    question. A combined chronology belongs with them too.
    """
    statements = [
        "v. 50 no. 3 - v. 52 no. 3 (Sep 1944 - Aug 1945)",
        "v. 92 no. 1 - v. 93 no. 3 (Winter 1986 - Summer 1987)",
        "v. 43 no. 6 - v. 43 no. 7 (June 2022 - July/August 2022)",
    ]
    groups = detect_patterns(statements)
    assert len(groups) == 1
    assert groups[0].count == 3
    assert groups[0].match_rate == 1.0


def test_a_generated_pattern_matches_a_season_where_it_saw_a_month():
    """
    The emitted expression is the general chronology pattern, not the forms
    observed, so a pattern confirmed from months still reads the season a later
    record writes in the same slot.
    """
    group = detect_patterns(["v. 9 no. 1 (Nov 1902)"])[0]
    compiled = re.compile(group.regex, re.IGNORECASE)
    assert compiled.fullmatch("v. 12 no. 4 (Winter 2001)")
    assert compiled.fullmatch("v. 12 no. 4 (Jul/Aug 2001)")


def test_free_text_is_visible_in_the_label():
    """
    _compact_label had no branch for UNKNOWN, so it omitted free text entirely:
    "v. 58 Suppl. (Sep 2003)" and "v. 58 (Sep 2003)" showed the identical label,
    and two different clusters could appear as the same row on screen.
    """
    plain = detect_patterns(["v. 58 (Sep 2003)"])[0].human_label
    noisy = detect_patterns(["v. 58 Suppl. (Sep 2003)"])[0].human_label
    assert plain != noisy
    assert "text" in noisy
