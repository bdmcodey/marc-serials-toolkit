"""
pattern_library.py
------------------
The set of patterns a cataloguer has confirmed, and the JSON they travel in.

A pattern is inert until confirmed.  Confirmation means every capture group has
been given a MARC role, so validation here is not paperwork: an unresolved role
or a duplicated one would put a value in the wrong subfield of every record the
pattern touches, silently and identically.  Everything rejected is reported.

The library is per-session and is exported as a file, so a cataloguer can build
one for a collection and load it again next week.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from pattern_bridge import (
    BOUNDARY_START,
    GroupRole,
    KIND_ENUM,
    KIND_IGNORE,
    KIND_UNRESOLVED,
    VALID_BOUNDARIES,
    VALID_KINDS,
    assign_levels,
    merge_roles,
)

SCHEMA_VERSION = 1

# The pattern detector's own Test button refuses anything longer, so a pattern
# over this length could never be checked against real statements before being
# trusted.  Matching the cap keeps "testable" and "usable" the same set.
MAX_REGEX_CHARS = 2000

MAX_PATTERNS = 200


@dataclass
class ConfirmedPattern:
    """One pattern a cataloguer has confirmed, with its roles."""
    id: str
    label: str
    regex: str
    roles: list[GroupRole] = field(default_factory=list)
    split: bool = True          # must match the detection run it came from
    priority: int = 0           # higher is tried first; cluster size by default
    notes: str = ""
    # Recognise these statements and convert none of them: the cataloguer will
    # handle this shape by hand. A judgement about their collection, so it is
    # stored with the pattern and survives an export.
    skip: bool = False
    _compiled: Optional["re.Pattern"] = field(default=None, repr=False, compare=False)

    def compiled(self) -> "re.Pattern":
        """The compiled regex, built once and reused across statements."""
        if self._compiled is None:
            self._compiled = re.compile(self.regex, re.IGNORECASE)
        return self._compiled

    def to_dict(self) -> dict:
        return {
            "id":       self.id,
            "label":    self.label,
            "regex":    self.regex,
            "roles":    [r.to_dict() for r in self.roles],
            "split":    self.split,
            "priority": self.priority,
            "notes":    self.notes,
            "skip":     self.skip,
        }


def _group_names(compiled: "re.Pattern") -> list[str]:
    """Named groups in the order they appear in the pattern."""
    return sorted(compiled.groupindex, key=lambda n: compiled.groupindex[n])


def validate_pattern(data: Any) -> tuple[Optional[ConfirmedPattern], list[str]]:
    """
    Build a ConfirmedPattern from untrusted input, or explain why not.

    Returns (pattern, errors).  A pattern is returned only when errors is empty.
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return None, ["Pattern entry is not an object."]

    label = str(data.get("label") or "").strip() or "Unnamed pattern"
    regex = data.get("regex")

    if not isinstance(regex, str) or not regex.strip():
        return None, [f"'{label}': no regular expression given."]
    if len(regex) > MAX_REGEX_CHARS:
        return None, [
            f"'{label}': the expression is {len(regex):,} characters; the limit "
            f"is {MAX_REGEX_CHARS:,}, above which it cannot be tested."
        ]
    try:
        compiled = re.compile(regex, re.IGNORECASE)
    except re.error as exc:
        return None, [f"'{label}': the expression could not be read ({exc})."]

    names = _group_names(compiled)
    if not names:
        return None, [
            f"'{label}': the expression captures nothing, so there is nothing "
            "to encode. Named groups such as (?P<start_vol>...) are required."
        ]

    raw_roles = data.get("roles")
    if not isinstance(raw_roles, list):
        return None, [f"'{label}': no roles given for the captured values."]

    # A skipped pattern converts nothing, so the rules about every value having
    # a meaning do not apply to it. That is not a loophole: "I cannot tell what
    # this shape means" is one of the better reasons to leave it alone, and
    # requiring the decisions first would make skipping useless exactly where
    # it is wanted. Un-skipping puts the pattern back through these checks.
    skip = bool(data.get("skip"))

    roles: list[GroupRole] = []
    known = set(names)
    seen_groups: set[str] = set()
    decided: set[str] = set()
    claimed: dict[tuple, str] = {}

    # Levels are settled before anything is checked: a screen that leaves the
    # level to position sends none, and two of those must not look like two
    # claims on the same subfield.
    incoming: list[GroupRole] = []
    for entry in raw_roles:
        if not isinstance(entry, dict):
            errors.append(f"'{label}': a role entry is not an object.")
            continue
        incoming.append(GroupRole.from_dict(entry))
    assign_levels(incoming)

    for role in incoming:
        decided.add(role.group)
        if role.group not in known:
            errors.append(
                f"'{label}': role given for '{role.group}', which the expression "
                "does not capture."
            )
            continue
        if role.group in seen_groups:
            errors.append(f"'{label}': '{role.group}' has more than one role.")
            continue
        if role.boundary not in VALID_BOUNDARIES:
            errors.append(
                f"'{label}': '{role.group}' has an unknown boundary "
                f"'{role.boundary}' (expected start or end)."
            )
            continue
        if role.kind == KIND_UNRESOLVED:
            if not skip:
                errors.append(
                    f"'{label}': '{role.group}' has no level decided. Every "
                    "captured value needs one, or 'Not encoded'."
                )
                continue
            seen_groups.add(role.group)
            roles.append(role)
            continue
        if role.kind not in VALID_KINDS:
            errors.append(
                f"'{label}': '{role.group}' has an unknown kind '{role.kind}'."
            )
            continue
        if role.kind != KIND_IGNORE:
            # Two values cannot share a subfield. For enumeration that means
            # the same boundary and the same *level*; the caption word may
            # repeat freely, since it names a level rather than being one.
            key = ((role.boundary, role.kind, role.level)
                   if role.kind == KIND_ENUM else (role.boundary, role.kind))
            if key in claimed:
                where = (f"{role.boundary} enumeration level {(role.level or 0) + 1}"
                         if role.kind == KIND_ENUM
                         else f"{role.boundary} {role.kind}")
                errors.append(
                    f"'{label}': '{role.group}' and '{claimed[key]}' are both set "
                    f"to the {where}; only one value can go in that subfield."
                )
                continue
            claimed[key] = role.group
        seen_groups.add(role.group)
        roles.append(role)

    missing = [n for n in names if n not in decided]
    if missing and not skip:
        errors.append(
            f"'{label}': no role decided for {', '.join(missing)}. Every captured "
            "value needs a level, or 'Not encoded'."
        )

    if not claimed and not skip:
        errors.append(
            f"'{label}': every captured value is set to 'Not encoded', so the "
            "pattern would produce no holdings."
        )

    if errors:
        return None, errors

    pattern_id = str(data.get("id") or "").strip() or uuid.uuid4().hex[:12]
    try:
        priority = int(data.get("priority") or 0)
    except (TypeError, ValueError):
        priority = 0

    return ConfirmedPattern(
        id=pattern_id,
        label=label,
        regex=regex,
        roles=roles,
        split=bool(data.get("split", True)),
        priority=priority,
        notes=str(data.get("notes") or "").strip(),
        skip=skip,
    ), []


def order_patterns(patterns: Sequence[ConfirmedPattern]) -> list[ConfirmedPattern]:
    """
    The order patterns are tried in: highest priority first, ties in the order
    they were confirmed.  Priority defaults to the size of the cluster the
    pattern came from, so the shape that covers the most holdings is tried first.
    """
    return [p for _, p in sorted(enumerate(patterns),
                                 key=lambda pair: (-pair[1].priority, pair[0]))]


def load_patterns(entries: Any) -> tuple[list[ConfirmedPattern], list[str]]:
    """Validate a list of pattern dicts, keeping the good and reporting the bad."""
    if not isinstance(entries, list):
        return [], ["The pattern list is not a list."]

    patterns: list[ConfirmedPattern] = []
    errors: list[str] = []
    seen_ids: set[str] = set()

    for entry in entries[:MAX_PATTERNS]:
        pattern, errs = validate_pattern(entry)
        if pattern is None:
            errors.extend(errs)
            continue
        while pattern.id in seen_ids:
            pattern.id = uuid.uuid4().hex[:12]
        seen_ids.add(pattern.id)
        patterns.append(pattern)

    if isinstance(entries, list) and len(entries) > MAX_PATTERNS:
        errors.append(
            f"Only the first {MAX_PATTERNS} patterns were read; "
            f"{len(entries) - MAX_PATTERNS} more were ignored."
        )

    return order_patterns(patterns), errors


def to_export(patterns: Sequence[ConfirmedPattern]) -> dict:
    """The library as a versioned, self-describing JSON document."""
    return {
        "schema":     SCHEMA_VERSION,
        "generated":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool":       "MARC Serials Toolkit — Holdings Workbench",
        "patterns":   [p.to_dict() for p in patterns],
    }


def from_export(document: Any) -> tuple[list[ConfirmedPattern], list[str]]:
    """
    Read an exported library back.

    Accepts either the full document or a bare list of patterns, so a file
    hand-edited down to its patterns still loads.
    """
    if isinstance(document, (str, bytes)):
        try:
            document = json.loads(document)
        except ValueError as exc:
            return [], [f"The file is not valid JSON ({exc})."]

    if isinstance(document, list):
        return load_patterns(document)

    if not isinstance(document, dict):
        return [], ["The file does not contain a pattern library."]

    schema = document.get("schema")
    if schema is not None and schema != SCHEMA_VERSION:
        return [], [
            f"This library was written for version {schema} of the format; "
            f"this tool reads version {SCHEMA_VERSION}."
        ]

    return load_patterns(document.get("patterns"))


def refresh_roles(pattern: ConfirmedPattern, regex: str) -> list[GroupRole]:
    """
    Roles for an edited regex, keeping every decision that still applies.

    Groups that survive the edit keep their level; new ones arrive with the
    inferred default and have to be decided before the pattern can be confirmed.
    """
    compiled = re.compile(regex, re.IGNORECASE)
    return merge_roles(_group_names(compiled), pattern.roles)
