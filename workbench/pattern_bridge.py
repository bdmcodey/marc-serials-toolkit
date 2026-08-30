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
from typing import Iterable, Optional, Sequence

from holdings_parser import (
    EnumChron,
    HoldingsRange,
    ParseResult,
    parse_866,
    # Private only by convention, and deliberately reused: months, seasons and
    # combined issues ("Jan/Feb" -> "01/02") must encode exactly as they do on
    # the parser path.  A second copy of those tables would drift.
    _chron_unit_value,
)
from pattern_detector import split_multi_range


# ── Roles ─────────────────────────────────────────────────────────────────────

LEVEL_VOL     = "vol"
LEVEL_ISSUE   = "issue"
LEVEL_PART    = "part"
LEVEL_YEAR    = "year"
LEVEL_MONTH   = "month"
LEVEL_IGNORE  = "ignore"        # captured, deliberately not encoded

# Chosen by the cataloguer, never by inference: the pattern captured something
# whose level cannot be read off the statement's structure.
LEVEL_UNRESOLVED = "unresolved"

# The levels a confirmed pattern may use.  UNRESOLVED is not among them --
# a pattern carrying one is not ready to be confirmed.
ENCODABLE_LEVELS = (LEVEL_VOL, LEVEL_ISSUE, LEVEL_PART, LEVEL_YEAR, LEVEL_MONTH)
VALID_LEVELS     = ENCODABLE_LEVELS + (LEVEL_IGNORE,)

BOUNDARY_START = "start"
BOUNDARY_END   = "end"
VALID_BOUNDARIES = (BOUNDARY_START, BOUNDARY_END)

# Level -> the EnumChron attribute it fills.
_LEVEL_ATTR = {
    LEVEL_VOL:   "vol",
    LEVEL_ISSUE: "issue",
    LEVEL_PART:  "part",
    LEVEL_YEAR:  "year",
    LEVEL_MONTH: "month",
}

# How the detector names the slot in "{context}_{slot}[_N]".  A bare NUMBER with
# no caption in front of it is named "num", and that is precisely the case the
# parser refuses to guess at (holdings_parser._parse_degenerate), so it is left
# unresolved for the cataloguer rather than defaulted to a level.
_SLOT_LEVEL = {
    "vol":   LEVEL_VOL,
    "iss":   LEVEL_ISSUE,
    "part":  LEVEL_PART,
    "year":  LEVEL_YEAR,
    "month": LEVEL_MONTH,
    "num":   LEVEL_UNRESOLVED,
}

# Cataloguer-facing names for the levels, used by the confirmation screen.
LEVEL_LABELS = {
    LEVEL_VOL:        "Volume",
    LEVEL_ISSUE:      "Issue / number",
    LEVEL_PART:       "Part",
    LEVEL_YEAR:       "Year",
    LEVEL_MONTH:      "Month / season",
    LEVEL_IGNORE:     "Not encoded",
    LEVEL_UNRESOLVED: "Not yet decided",
}

_TRAILING_INDEX_RE = re.compile(r"_(\d+)$")


@dataclass
class GroupRole:
    """What one regex capture group means in MARC terms."""
    group: str                       # the named group, e.g. "end_year_2"
    boundary: str = BOUNDARY_START   # start | end
    level: str = LEVEL_UNRESOLVED    # see VALID_LEVELS

    @property
    def resolved(self) -> bool:
        return self.level in VALID_LEVELS

    @property
    def encodes(self) -> bool:
        return self.level in ENCODABLE_LEVELS

    def to_dict(self) -> dict:
        return {"group": self.group, "boundary": self.boundary,
                "level": self.level, "level_label": LEVEL_LABELS.get(self.level, self.level)}

    @classmethod
    def from_dict(cls, data: dict) -> "GroupRole":
        return cls(
            group=str(data.get("group") or ""),
            boundary=str(data.get("boundary") or BOUNDARY_START).strip().lower(),
            level=str(data.get("level") or LEVEL_UNRESOLVED).strip().lower(),
        )


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

    The level comes from the detector's own naming.  The boundary does not:
    within a level, the *first* group to appear in the statement is the start
    boundary and the second is the end boundary, whatever the detector called
    them.  The detector flips its "start"/"end" context at the first top-level
    hyphen, which is right for "v.1(1990)-v.5(1994)" and wrong for
    "v.1-5(1990-1994)", where the hyphen it flips on is the volume range and
    both years are consequently named end_year.  Position is the more reliable
    signal, and it is what makes the common corrections unnecessary.

    A third or later group at the same level has no defensible default, so it
    is offered as "not encoded" rather than guessed at.
    """
    roles: list[GroupRole] = []
    seen: dict[str, int] = {}

    for name in named_groups:
        level = _SLOT_LEVEL.get(_slot_of(name), LEVEL_UNRESOLVED)
        if level not in ENCODABLE_LEVELS:
            roles.append(GroupRole(group=name, boundary=BOUNDARY_START, level=level))
            continue

        n = seen.get(level, 0)
        seen[level] = n + 1
        if n == 0:
            roles.append(GroupRole(name, BOUNDARY_START, level))
        elif n == 1:
            roles.append(GroupRole(name, BOUNDARY_END, level))
        else:
            roles.append(GroupRole(name, BOUNDARY_START, LEVEL_IGNORE))

    return roles


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
    return out


# ── Building a ParseResult from a match ───────────────────────────────────────

_OPEN_ENDED_RE = re.compile(r"-\s*$")


def _value_for(level: str, raw: str) -> str:
    """
    The value to store for a captured string at `level`.

    Chronology goes through the parser's own encoder so "Jan." becomes "01",
    "Winter" becomes "24" and "Jan/Feb" becomes "01/02", exactly as on the
    parser path.  Everything else is stored as written.
    """
    if level == LEVEL_MONTH:
        return _chron_unit_value(raw)
    return raw


def _range_from_match(segment: str, match: "re.Match",
                      roles: Sequence[GroupRole],
                      warnings: list[str]) -> Optional[HoldingsRange]:
    """Assemble one HoldingsRange from a match and the confirmed roles."""
    captured = match.groupdict()
    start, end = EnumChron(), EnumChron()

    for role in roles:
        raw = (captured.get(role.group) or "").strip()
        if not raw:
            continue
        if not role.encodes:
            warnings.append(
                f"'{raw}' was matched by the pattern but is not encoded: "
                f"its role is set to '{LEVEL_LABELS.get(role.level, role.level)}'."
            )
            continue
        target = start if role.boundary == BOUNDARY_START else end
        setattr(target, _LEVEL_ATTR[role.level], _value_for(role.level, raw))

    if not (start.has_enum() or start.has_chron()
            or end.has_enum() or end.has_chron()):
        return None

    return HoldingsRange(
        start=start,
        end=end if (end.has_enum() or end.has_chron()) else None,
        open_ended=bool(_OPEN_ENDED_RE.search(segment)),
        raw=segment,
    )


def build_parse_result(
    text: str,
    compiled: "re.Pattern",
    roles: Sequence[GroupRole],
    split: bool = True,
) -> Optional[ParseResult]:
    """
    Parse `text` with a confirmed pattern, or return None if it does not apply.

    Multi-range statements are split first, so "v.1(1990)-v.3(1992), v.5(1994)-"
    produces two HoldingsRanges under one ParseResult -- the same shape the
    parser produces, which is what lets the converter take it unchanged.

    A segment the pattern does not match is handed to parse_866() rather than
    dropped: a pattern that covers most of a statement must not cost the rest of
    it.  None is returned only when *no* segment matched, so the caller can fall
    back to the parser for the whole statement -- the block grammar and the
    degenerate forms are reached that way and must stay reachable.
    """
    text = (text or "").strip()
    if not text:
        return None

    segments = [s for s in (split_multi_range(text) if split else [text]) if s.strip()]
    result = ParseResult(raw=text)
    any_match = False

    for seg in segments:
        seg = seg.strip()
        m = compiled.fullmatch(seg) or compiled.search(seg)
        if m is None:
            fallback = parse_866(seg)
            if fallback.ranges:
                result.ranges.extend(fallback.ranges)
                result.warnings.extend(fallback.warnings)
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
        hr = _range_from_match(seg, m, roles, result.warnings)
        if hr is not None:
            result.ranges.append(hr)
        else:
            result.warnings.append(
                f"The pattern matched '{seg}' but captured nothing to encode."
            )

    if not any_match or not result.ranges:
        return None
    return result


# ── Applying a library ────────────────────────────────────────────────────────

PARSER_SOURCE = "parser"


def apply_patterns(text: str, patterns: Sequence) -> tuple[ParseResult, str]:
    """
    Convert one statement with the first confirmed pattern that matches it.

    `patterns` is a sequence of pattern_library.ConfirmedPattern, already in the
    order they should be tried.  Returns the ParseResult and the id of whatever
    produced it, so the screen can tell the cataloguer which pattern was used --
    or that the standard parser was.
    """
    for pattern in patterns:
        try:
            compiled = pattern.compiled()
        except re.error:
            continue                      # validated on entry; never fatal here
        result = build_parse_result(text, compiled, pattern.roles, pattern.split)
        if result is not None:
            return result, pattern.id

    return parse_866(text), PARSER_SOURCE
