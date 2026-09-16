"""
pattern_bridge.py
-----------------
Turns a pattern detector regex, plus a cataloguer's confirmation of what each
capture group *means*, into the same ParseResult the holdings parser produces.

This is the join between the two tools.  The detector knows how a statement is
shaped; it does not know which captured number is a volume and which is an
issue, nor which year opens a range and which closes it.  A cataloguer does.
Once they have said so, every capture group has a MARC role and the existing
converter can take it from there unchanged.

Nothing here replaces holdings_parser.parse_866().  A statement no confirmed
pattern matches is still parsed by it, and the chronology encoding is borrowed
from it rather than reimplemented, so a month coded through a pattern and a
month coded through the parser come out identical.

Public API
----------
    infer_roles(named_groups)          -> [GroupRole, ...]   (defaults to offer)
    build_parse_result(text, rx, roles) -> ParseResult | None
    apply_patterns(text, patterns)      -> (ParseResult, source_id)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Optional, Sequence

from marc_serials.parser import (
    EnumChron,
    EnumLevel,
    HoldingsRange,
    ParseResult,
    is_distributed_list,
    chron_unit_code,
    normalise_year,
    parse_866,
    # Private only by convention, and deliberately reused: months, seasons and
    # combined issues ("Jan/Feb" -> "01/02") must encode exactly as they do on
    # the parser path.  A second copy of those tables would drift.
    _chron_unit_value,
)
from marc_serials.detector import split_multi_range


@lru_cache(maxsize=4096)
def is_more_than_one_run(segment: str) -> bool:
    """
    Whether this segment holds more runs of holdings than a pattern can describe.

    A confirmed pattern describes exactly one: its roles carry a boundary and a
    level, but nothing says *which run* a capture opens, so where a statement
    has several the pattern pairs the first value with the last and sends
    everything between to "not encoded".

    0.8.6 asked this question of comma lists only -- `is_distributed_list()` --
    which is the shape that prompted it, and not the only one that has it.  The
    chronology-first format is another: a pattern confirmed for
    "N1984: (2 (1))M1985: 2 (2 [summer])" claims it and writes *no 863 at all*,
    where the parser writes the two it holds.  The general question is the one
    worth asking, and the parser already answers it -- it is the thing that
    knows how many runs a statement has.

    Cached because it is asked once per pattern per statement while the library
    is tried in order, and the answer depends on the text alone: a hundred
    patterns over a hundred statements is ten thousand identical parses, which
    measured at 0.6 seconds added to a conversion before the cache.
    """
    if is_distributed_list(segment):
        return True                      # cheap, and true by construction
    return len(parse_866(segment).ranges) > 1


def split_statement(text: str) -> list[str]:
    """
    Split a statement into the units the confirm step and the patterns see.

    The detector's own splitter cuts at every top-level comma that looks like a
    separator, which turns "v. 19 nos. 1, 3, 5, 7-12 (Jan, Mar, May, Jul-Dec
    1915)" into "v. 19 nos. 1", "3", "5" and "7-12 (Jan, Mar, May, Jul-Dec
    1915)" -- four fragments, three of which mean nothing on their own and are
    then offered to the cataloguer as three shapes to confirm.

    A statement listing several runs of holdings is one statement, and the
    parser reads it whole.  The detector stays independent of the parser, so the
    rule lives here, where both are already in scope.
    """
    if is_distributed_list(text):
        return [text.strip()]
    return split_multi_range(text)


# ── Roles ─────────────────────────────────────────────────────────────────────
#
# A captured value is one of three things: an enumeration level, a chronology
# level, or something not encoded.  Enumeration levels have no names -- only a
# position in the hierarchy and a caption word -- which is why they carry both
# separately, and why the cataloguer can change either.

KIND_ENUM    = "enum"
KIND_YEAR    = "year"
KIND_MONTH   = "month"
KIND_DAY     = "day"
KIND_IGNORE  = "ignore"        # captured, deliberately not encoded

# Chosen by the cataloguer, never by inference: the pattern captured something
# whose level cannot be read off the statement's structure.
KIND_UNRESOLVED = "unresolved"

ENCODABLE_KINDS = (KIND_ENUM, KIND_YEAR, KIND_MONTH, KIND_DAY)
VALID_KINDS     = ENCODABLE_KINDS + (KIND_IGNORE,)

BOUNDARY_START = "start"
BOUNDARY_END   = "end"
VALID_BOUNDARIES = (BOUNDARY_START, BOUNDARY_END)

# Captions a cataloguer can pick from, in the words they already use.  Picking
# one sets the *caption*; the level comes from where the value sits in the
# statement, and either can be overridden.
CAPTION_CHOICES = (
    ("v.",   "Volume"),
    ("no.",  "Issue / number"),
    ("pt.",  "Part"),
    ("ser.", "Series"),
)
CAPTION_LABELS = dict(CAPTION_CHOICES)

# How the detector names the slot in "{context}_{slot}[_N]", and what each
# suggests.  A bare NUMBER with no caption in front of it is named "num", which
# is precisely the case the parser refuses to guess at, so it is left
# unresolved for the cataloguer rather than defaulted.
_SLOT_KIND = {
    "vol":   (KIND_ENUM, "v."),
    "iss":   (KIND_ENUM, "no."),
    "part":  (KIND_ENUM, "pt."),
    "year":  (KIND_YEAR, None),
    "month": (KIND_MONTH, None),
    # The detector has no day token -- a day is a bare NUMBER -- so nothing
    # infers one. A cataloguer who recognises one says so; that is what the
    # confirm step is for.
    "num":   (KIND_UNRESOLVED, None),
}

# Cataloguer-facing names for what a value is, used by the confirmation screen.
KIND_LABELS = {
    KIND_ENUM:       "Enumeration",
    KIND_YEAR:       "Year",
    KIND_MONTH:      "Month / season",
    KIND_DAY:        "Day",
    KIND_IGNORE:     "Not encoded",
    KIND_UNRESOLVED: "Not yet decided",
}

# Legacy role vocabulary, still found in exported pattern libraries.
_LEGACY_LEVELS = {
    "vol":   (KIND_ENUM, "v."),
    "issue": (KIND_ENUM, "no."),
    "part":  (KIND_ENUM, "pt."),
    "year":  (KIND_YEAR, None),
    "month": (KIND_MONTH, None),
    "day":   (KIND_DAY, None),
    "ignore": (KIND_IGNORE, None),
    "unresolved": (KIND_UNRESOLVED, None),
}

_TRAILING_INDEX_RE = re.compile(r"_(\d+)$")


@dataclass
class GroupRole:
    """What one regex capture group means in MARC terms."""
    group: str                       # the named group, e.g. "end_year_2"
    boundary: str = BOUNDARY_START   # start | end
    kind: str = KIND_UNRESOLVED      # see VALID_KINDS
    # Enumeration only: which level of the hierarchy, 0 being the most
    # significant, and the caption word that level carries.  Both are the
    # cataloguer's to change -- the level decides the subfield, the caption
    # decides what the 853 calls it.
    level: Optional[int] = None
    caption: Optional[str] = None
    # True when the kind was read off a cataloguing convention rather than off
    # the statement -- a usable default to show, but not one to act on unasked.
    suggested: bool = False

    @property
    def resolved(self) -> bool:
        return self.kind in VALID_KINDS

    @property
    def needs_a_decision(self) -> bool:
        """Unresolved, or resolved only by convention and not yet confirmed."""
        return self.kind == KIND_UNRESOLVED or self.suggested

    @property
    def encodes(self) -> bool:
        return self.kind in ENCODABLE_KINDS

    def to_dict(self) -> dict:
        return {"group": self.group, "boundary": self.boundary,
                "kind": self.kind, "level": self.level,
                "caption": self.caption, "suggested": self.suggested,
                "kind_label": KIND_LABELS.get(self.kind, self.kind),
                "caption_label": CAPTION_LABELS.get(self.caption or "", "")}

    @classmethod
    def from_dict(cls, data: dict) -> "GroupRole":
        kind = str(data.get("kind") or "").strip().lower()
        caption = data.get("caption")
        # A library exported before enumeration became positional stores
        # "level": "vol". Read it as the caption it was really choosing; the
        # position is recomputed from the group order.
        if not kind:
            legacy = str(data.get("level") or "").strip().lower()
            kind, legacy_caption = _LEGACY_LEVELS.get(
                legacy, (KIND_UNRESOLVED, None))
            caption = caption or legacy_caption
            level = None
        else:
            raw_level = data.get("level")
            level = raw_level if isinstance(raw_level, int) else None
        return cls(
            group=str(data.get("group") or ""),
            boundary=str(data.get("boundary") or BOUNDARY_START).strip().lower(),
            kind=kind or KIND_UNRESOLVED,
            level=level,
            caption=str(caption).strip() if caption else None,
            suggested=bool(data.get("suggested")),
        )


def assign_levels(roles: Sequence[GroupRole]) -> list[GroupRole]:
    """
    Number the enumeration levels of each boundary by the order they appear.

    Position in the statement is the level, so the first enumeration value on a
    boundary is level 0 whatever its caption says.  A level the cataloguer has
    set by hand is left alone; the rest fill the gaps around it in order.
    """
    for boundary in VALID_BOUNDARIES:
        enum_roles = [r for r in roles
                      if r.kind == KIND_ENUM and r.boundary == boundary]
        taken = {r.level for r in enum_roles if r.level is not None}
        nxt = 0
        for role in enum_roles:
            if role.level is not None:
                continue
            while nxt in taken:
                nxt += 1
            role.level = nxt
            taken.add(nxt)
    return list(roles)


def _slot_of(name: str) -> str:
    """
    The slot portion of a detector group name: "end_year_2" -> "year".

    Returns "" for any name the detector did not generate -- a cataloguer may
    edit the regex by hand, and an invented group name has no inferable meaning.
    """
    base = _TRAILING_INDEX_RE.sub("", name)
    head, sep, tail = base.partition("_")
    if not sep or head not in VALID_BOUNDARIES:
        return ""
    return tail


def infer_roles(named_groups: Sequence[str]) -> list[GroupRole]:
    """
    Propose a role for every capture group, for the cataloguer to confirm.

    Kind and boundary are read from the group's name, but the boundary is
    recomputed rather than trusted: within a level, the *first* group to appear
    is the start boundary and the second is the end.  The detector names groups
    by the same rule, so on its own output the two agree everywhere -- but a
    cataloguer may edit the expression by hand, and a library exported before
    the detector counted per level may still name two values end_year.

    Enumeration levels are numbered by the order they appear, because that
    order *is* the hierarchy.  The caption each one suggests comes from the
    caption word the detector saw, so a statement written with "no." is offered
    "no." rather than being told it is really a volume.

    A third or later group at the same chronology level has no defensible
    default, so it is offered as "not encoded" rather than guessed at.
    """
    roles: list[GroupRole] = []
    seen: dict[str, int] = {}
    slots = [_slot_of(n) for n in named_groups]

    for i, name in enumerate(named_groups):
        slot = slots[i]
        kind, caption = _SLOT_KIND.get(slot, (KIND_UNRESOLVED, None))
        suggested = False

        # A captionless number sitting immediately above a captioned one is an
        # enumeration level: "39 no 1" numbers by volume then issue.  The
        # statement does not say so, so it is offered as a default and still
        # asked about -- unlike a captioned value, which says what it is.
        if kind == KIND_UNRESOLVED and slot == "num" \
                and i + 1 < len(slots) and slots[i + 1] == "iss":
            kind, caption, suggested = KIND_ENUM, "v.", True

        if kind not in ENCODABLE_KINDS:
            roles.append(GroupRole(group=name, boundary=BOUNDARY_START,
                                   kind=kind))
            continue

        # Enumeration repeats per boundary; chronology gets one start and one
        # end, and a third value at the same level is not encodable.
        counter = f"{kind}:{caption}" if kind == KIND_ENUM else kind
        n = seen.get(counter, 0)
        seen[counter] = n + 1
        if n == 0:
            boundary = BOUNDARY_START
        elif n == 1:
            boundary = BOUNDARY_END
        else:
            roles.append(GroupRole(group=name, boundary=BOUNDARY_START,
                                   kind=KIND_IGNORE))
            continue
        roles.append(GroupRole(name, boundary, kind,
                               caption=caption, suggested=suggested))

    return assign_levels(roles)


def roles_from_regex(regex: str) -> list[GroupRole]:
    """Infer roles straight from a regex string, in group-number order."""
    compiled = re.compile(regex, re.IGNORECASE)
    names = sorted(compiled.groupindex, key=lambda n: compiled.groupindex[n])
    return infer_roles(names)


def merge_roles(named_groups: Sequence[str],
                existing: Iterable[GroupRole]) -> list[GroupRole]:
    """
    Re-infer roles for `named_groups`, keeping any choice already made.

    Used when a cataloguer edits a pattern's regex after assigning roles: the
    groups that survived the edit keep their meaning, new ones get a default.
    """
    kept = {r.group: r for r in existing}
    out = []
    for role in infer_roles(named_groups):
        prior = kept.get(role.group)
        out.append(prior if prior is not None else role)
    return assign_levels(out)


# ── Building a ParseResult from a match ───────────────────────────────────────

_OPEN_ENDED_RE = re.compile(r"-\s*$")


def _value_for(kind: str, raw: str) -> str:
    """
    The value to store for a captured string of `kind`.

    Chronology goes through the parser's own encoder so "Jan." becomes "01",
    "Winter" becomes "24" and "Jan/Feb" becomes "01/02", exactly as on the
    parser path.  Everything else is stored as written.
    """
    if kind == KIND_MONTH:
        return _chron_unit_value(raw)
    if kind == KIND_YEAR:
        # "1996/97" -> "1996/1997", as on the parser path.
        return normalise_year(raw)
    return raw


# A word running straight into a captured chronology: the "Late" of "Late
# Summer", the "Early" of "Early Spring", the "mid" of "mid-July". Letters, then
# an optional hyphen, then optional spaces, then the capture -- a separator or a
# bracket in between means the word belongs to something else.
#
# The hyphen is captured separately and never included in the word, because it
# is the one character that can mean either half of this. "mid-July" is a
# qualified month; the "Feb-" of "(Jan/Feb-July/Aug 1985)" is a month and a
# *range separator*, and reading it as a qualifier made the value
# "Feb- July/Aug" and refused a statement that had been converting correctly.
# What tells them apart is whether the word is itself a month or a season.
_CHRON_QUALIFIER_RE = re.compile(r"([A-Za-z][A-Za-z.'\u2019]*)(-?)\s*$")


def _chron_qualifier(segment: str, match: "re.Match", group: str) -> str:
    """
    The word qualifying a captured chronology unit, or "".

    The detector's CHRON token matches a month or season name wherever it finds
    one, so "Late Summer" captures "Summer" and leaves "Late" as literal text in
    the pattern -- text the cataloguer never sees a decision about, and which
    then disappears.  The 863 came out "$j 22" and said the run ended in Summer
    2002.

    Whether that is right is not something this tool can settle.  "Late Summer"
    may be the Summer issue; the serial may equally have an Early Summer as
    well, and coding both 22 would merge two issues into one.  A cataloguer has
    to look at the piece.  So the qualifier is put back on the value, which
    sends it through exactly the check the standard parser applies -- the
    subfield holds MARC codes, "Late Summer" is not one, so it is named rather
    than written, and the record is flagged for review.
    """
    try:
        start = match.start(group)
    except (IndexError, re.error):               # pragma: no cover - unnamed
        return ""
    if start <= 0:
        return ""
    found = _CHRON_QUALIFIER_RE.search(segment[:start])
    if not found:
        return ""
    word, hyphen = found.group(1), found.group(2)
    if hyphen and chron_unit_code(word):
        # "Jan/Feb-July/Aug": the hyphen separates two chronologies, and the
        # word before it is the first of them, not a qualifier on the second.
        return ""
    return word + hyphen


def _range_from_match(segment: str, match: "re.Match",
                      roles: Sequence[GroupRole],
                      warnings: list[str],
                      undecided: Optional[list] = None) -> Optional[HoldingsRange]:
    """
    Assemble one HoldingsRange from a match and the confirmed roles.

    `undecided`, when given, collects values the pattern captured for a role
    nobody has decided about yet -- see the note where they are recorded.
    """
    captured = match.groupdict()
    start, end = EnumChron(), EnumChron()
    enum_slots: dict = {BOUNDARY_START: {}, BOUNDARY_END: {}}
    if undecided is None:
        undecided = []

    for role in roles:
        raw = (captured.get(role.group) or "").strip()
        if not raw:
            continue
        if not role.encodes:
            warnings.append(
                f"'{raw}' was matched by the pattern but is not encoded: "
                f"it is set to '{KIND_LABELS.get(role.kind, role.kind)}'."
            )
            # A value nobody has decided about is not a value anyone chose to
            # leave out. A stored pattern can never be in this state -- the
            # library refuses one -- so this is the preview screen, where the
            # honest answer is that the statement still needs a cataloguer.
            if role.kind == KIND_UNRESOLVED:
                undecided.append(raw)
            continue
        target = start if role.boundary == BOUNDARY_START else end
        if role.kind == KIND_ENUM:
            index = role.level if role.level is not None else len(
                enum_slots[role.boundary])
            enum_slots[role.boundary][index] = EnumLevel(
                caption=role.caption, value=raw)
        elif role.kind in (KIND_MONTH, KIND_DAY):
            # A qualifier the pattern matched as literal text is part of what
            # the piece says, and dropping it quietly is the one thing not to
            # do. Put back, the converter treats it as the parser does.
            qualifier = _chron_qualifier(segment, match, role.group)
            value = f"{qualifier} {raw}" if qualifier else raw
            setattr(target, role.kind, _value_for(role.kind, value))
        else:
            setattr(target, role.kind, _value_for(role.kind, raw))

    # Levels are written into the position the cataloguer gave them, and a gap
    # is filled with an empty level rather than shifting everything up -- the
    # position is the meaning.
    for boundary, slots in enum_slots.items():
        if not slots:
            continue
        target = start if boundary == BOUNDARY_START else end
        target.enum = [slots.get(i, EnumLevel())
                       for i in range(max(slots) + 1)]

    if not (start.has_enum() or start.has_chron()
            or end.has_enum() or end.has_chron()):
        return None

    return HoldingsRange(
        start=start,
        end=end if (end.has_enum() or end.has_chron()) else None,
        open_ended=bool(_OPEN_ENDED_RE.search(segment)),
        raw=segment,
    )


def _apply_confirmed_captions(result: ParseResult,
                              roles: Sequence[GroupRole]) -> None:
    """
    Put the cataloguer's captions onto ranges the parser produced.

    The parser reads a caption off the statement, which is right whenever the
    statement carries one. Where it does not -- a level written as a bare
    number -- the parser writes NO_CAPTION and the 853 declares "(*)". A
    confirmed pattern is exactly where the missing word lives, because a
    cataloguer supplied it, so it is filled in here.

    Only empty captions are filled. A caption the statement states is what the
    piece in hand actually says, and a pattern's generic "v." must not overwrite
    a "vol." that is printed on the volume.
    """
    by_slot: dict = {}
    for role in roles:
        if role.kind == KIND_ENUM and role.caption and role.level is not None:
            by_slot.setdefault((role.boundary, role.level), role.caption)
    if not by_slot:
        return

    for hr in result.ranges:
        for boundary, ec in ((BOUNDARY_START, hr.start), (BOUNDARY_END, hr.end)):
            if ec is None:
                continue
            for index, level in enumerate(ec.enum):
                if level.caption:
                    continue
                caption = by_slot.get((boundary, index))
                if caption:
                    level.caption = caption


def build_parse_result(
    text: str,
    compiled: "re.Pattern",
    roles: Sequence[GroupRole],
    split: bool = True,
    fallback: bool = True,
    defer_lists: bool = True,
) -> Optional[ParseResult]:
    """
    Parse `text`, using a confirmed pattern for what the parser cannot settle.

    There is one reader of holdings structure, parse_866(). Where it produces
    ranges they are what this returns, with the cataloguer's captions laid over
    any level the statement left unnamed.

    A pattern earns its keep on the statements the parser refuses. "?: 16"
    carries a number and nothing whatever to say whether it is a volume or an
    issue; no amount of parsing can settle that, because the information is not
    in the statement. A cataloguer has already said which it is, and
    _build_from_pattern() below writes it out.

    This ordering was measured rather than assumed. Across the 141 statements
    in the corpus and the two .mrc fixtures, the two readings agreed on 90 and
    disagreed on 10 -- and on 9 of those 10 the pattern was the wrong one, eight
    of them declaring a $j caption in the 853 that their own 863 never filled.
    Of the 5 statements only the pattern converted, 3 were supplements belonging
    in an 867 and one produced no fields at all.

    Whether the pattern *applies* is still the regex's question, and it is asked
    first. Every caller depends on that: `fallback=False` must write nothing for
    a statement no pattern matches, or the converter removes an 866 the
    cataloguer asked to keep, and a skip pattern must claim only statements it
    actually matches rather than everything the parser happens to read.
    """
    text = (text or "").strip()
    if not text:
        return None
    if not _pattern_applies(text, compiled, split, fallback, defer_lists):
        return None

    parsed = parse_866(text)
    if parsed.ranges:
        _apply_confirmed_captions(parsed, roles)
        return parsed
    return _build_from_pattern(text, compiled, roles, split, fallback,
                               defer_lists)


def _pattern_applies(text: str, compiled: "re.Pattern", split: bool,
                     fallback: bool, defer_lists: bool) -> bool:
    """
    Whether this pattern describes `text`, on the same terms it always has.

    A segment matches only when the pattern spans the whole of it: a partial
    match means the pattern does not describe this statement. With `fallback`,
    one matching segment is enough, because the parser reads the others; without
    it every segment must match, since half a statement is worse than none.
    """
    segments = [s.strip() for s in
                (split_statement(text) if split else [text]) if s.strip()]
    if not segments:
        return False

    matched = 0
    for seg in segments:
        applies = not (defer_lists and is_more_than_one_run(seg)) \
            and compiled.fullmatch(seg) is not None
        if applies:
            matched += 1
        elif not fallback:
            return False
    return matched > 0


def _build_from_pattern(
    text: str,
    compiled: "re.Pattern",
    roles: Sequence[GroupRole],
    split: bool = True,
    fallback: bool = True,
    defer_lists: bool = True,
) -> Optional[ParseResult]:
    """
    Assemble a ParseResult from the pattern's captures alone.

    Reached only for a statement parse_866() would write nothing for, so there
    is no reading to defer to and the cataloguer's confirmation is all there is.

    Multi-range statements are split first, so "v.1(1990)-v.3(1992), v.5(1994)-"
    produces two HoldingsRanges under one ParseResult -- the same shape the
    parser produces, which is what lets the converter take it unchanged.

    With `fallback`, a segment the pattern does not match is handed to
    parse_866() rather than dropped: a pattern that covers most of a statement
    must not cost the rest of it.  None is returned only when *no* segment
    matched, so the caller can fall back to the parser for the whole statement.

    Without it, a statement converts only when *every* segment matches. Half a
    statement is the one outcome worse than none: the 866 is removed once
    anything is written from it, so converting the first range of
    "v.1(1990)-v.3(1992) / v.5(1994)-v.8(1997)" and silently discarding the
    second would delete holdings. All or nothing keeps the field intact.

    A segment matches only when the pattern spans the whole of it.  The same
    argument that makes half a statement worse than none makes half a *segment*
    worse than none, and re.search would allow exactly that: the pattern for the
    common "v. 9 no. 1 (Nov 1902)" shape search-matches the tail of
    "v. 1 no. 1 (1995)-v. 12 no. 4 (December 2006)", and everything before the
    matched span -- the whole first boundary -- would be dropped with nothing
    written to say so.  A partial match means the pattern does not describe this
    statement, so it is treated as no match at all.
    """
    text = (text or "").strip()
    if not text:
        return None

    segments = [s for s in (split_statement(text) if split else [text]) if s.strip()]
    result = ParseResult(raw=text)
    any_match = False
    undecided: list[str] = []

    for seg in segments:
        seg = seg.strip()
        # A segment listing several runs of holdings is more ranges than a
        # pattern can describe. Roles carry a boundary and a level but no notion
        # of *which run* a capture opens, so the pattern pairs the first value
        # with the last and the runs between them go to "not encoded" -- "v. 19
        # nos. 1, 3, 5, 7-12 (Jan, Mar, May, Jul-Dec 1915)" came out as one
        # compressed 863 holding two of its twelve assertions. The parser reads
        # it as four 863s, so the pattern stands aside -- see
        # is_more_than_one_run() for why the test is not about lists.
        m = None if (defer_lists and is_more_than_one_run(seg)) \
            else compiled.fullmatch(seg)
        if m is None:
            if not fallback:
                # All or nothing: see the note above about half a statement.
                return None
            recovered = parse_866(seg)
            if recovered.ranges:
                result.ranges.extend(recovered.ranges)
                result.warnings.extend(recovered.warnings)
                result.warnings.append(
                    f"'{seg}' did not match the pattern; it was read by the "
                    "standard parser instead."
                )
            else:
                result.warnings.append(
                    f"'{seg}' matched neither the pattern nor the standard parser."
                )
            continue

        any_match = True
        hr = _range_from_match(seg, m, roles, result.warnings, undecided)
        if hr is not None:
            result.ranges.append(hr)
        else:
            result.warnings.append(
                f"The pattern matched '{seg}' but captured nothing to encode."
            )

    if not any_match or not result.ranges:
        return None
    result.needs_review = bool(undecided)
    return result


# ── Applying a library ────────────────────────────────────────────────────────

PARSER_SOURCE = "parser"

# No pattern matched and the parser was switched off: nothing was written.
UNMATCHED_SOURCE = "unmatched"

# A pattern matched, and the cataloguer has told it not to convert. Different
# from UNMATCHED in the one way that matters to them: this statement was
# recognised and deliberately left alone, rather than falling through unread.
SKIPPED_SOURCE = "skipped"


def apply_patterns(text: str, patterns: Sequence,
                   fallback: bool = True) -> tuple[ParseResult, str]:
    """
    Convert one statement with the first confirmed pattern that matches it.

    `patterns` is a sequence of pattern_library.ConfirmedPattern, already in the
    order they should be tried.  Returns the ParseResult and the id of whatever
    produced it, so the screen can tell the cataloguer which pattern was used --
    or that the standard parser was.

    `fallback` decides what happens to a statement no pattern matches.  With it,
    the standard parser reads the statement, which is the converter's own
    behaviour.  Without it nothing is written and the 866 is left exactly as it
    was -- for a cataloguer who wants only what their own confirmed patterns
    produce, and nothing decided on their behalf.

    A pattern marked `skip` claims the statements it describes and converts
    none of them.  Claiming matters: without it the statement would fall
    through to the standard parser and be converted anyway, which is the
    opposite of what skipping asks for.  It claims only a statement it matches
    *whole*, so marking one shape to be left alone cannot quietly capture a
    longer statement it merely begins.

    A statement listing several runs of holdings is handed to the parser even
    where a pattern matches it, because a pattern cannot describe more than one
    run -- and the cataloguer is told, since they confirmed that pattern and
    would otherwise see it quietly unused.
    """
    passed_over = ""

    for pattern in patterns:
        try:
            compiled = pattern.compiled()
        except re.error:
            continue                      # validated on entry; never fatal here
        if getattr(pattern, "skip", False):
            # Skipping is a decision about which statements the cataloguer will
            # handle by hand, so it claims a discontinuous list like any other
            # shape -- standing aside for the parser would convert the very
            # statement they asked to be left alone.
            claimed = build_parse_result(text, compiled, pattern.roles,
                                         pattern.split, fallback=False,
                                         defer_lists=False)
            if claimed is not None:
                return _untouched(
                    text,
                    f"'{pattern.label}' is set to be skipped, so this statement "
                    "was left exactly as it is."
                ), SKIPPED_SOURCE
            continue
        if not passed_over and is_more_than_one_run(text.strip()) \
                and compiled.fullmatch(text.strip()):
            passed_over = pattern.label

        result = build_parse_result(text, compiled, pattern.roles,
                                    pattern.split, fallback)
        if result is not None:
            return result, pattern.id

    if fallback:
        result = parse_866(text)
        if passed_over:
            result.warnings.append(
                f"'{passed_over}' matches this statement, but the statement "
                "holds several runs of holdings and a pattern describes one "
                "run. It was read by the standard parser instead, which "
                "records each run as its own 863."
            )
        return result, PARSER_SOURCE

    return _untouched(
        text,
        "No confirmed pattern matched this statement, and the standard parser "
        "was not applied. It has been left as it is."
    ), UNMATCHED_SOURCE


def _untouched(text: str, why: str) -> ParseResult:
    """A result that writes nothing, carrying the reason on the record."""
    result = ParseResult(raw=text)
    result.success = False
    result.warnings.append(why)
    return result
