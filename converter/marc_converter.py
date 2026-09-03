"""
marc_converter.py
-----------------
Converts ParseResult objects (from holdings_parser.py) into pymarc
Field objects:
  853 – Captions and Pattern (Basic Bibliographic Unit)
  863 – Enumeration and Chronology (Basic Bibliographic Unit)

References:
  MARC 21 Format for Holdings Data
  https://www.loc.gov/marc/holdings/
"""

from __future__ import annotations

import re
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

try:
    from pymarc import Field, Subfield, Record
    HAS_PYMARC = True
except ImportError:
    HAS_PYMARC = False

from holdings_parser import (ParseResult, HoldingsRange, EnumChron,
                             SEASON_CODES, MARC_CHRON_CODES)


# ---------------------------------------------------------------------------
# Frequency codes (853 $w)
# ---------------------------------------------------------------------------

FREQUENCY_CODES: Dict[str, str] = {
    "a": "Annual",
    "b": "Bimonthly (every 2 months)",
    "c": "Semiweekly",
    "d": "Daily",
    "e": "Biweekly (every 2 weeks)",
    "f": "Semiannual",
    "g": "Biennial",
    "h": "Triennial",
    "i": "Three times a week",
    "j": "Three times a month",
    "m": "Monthly",
    "q": "Quarterly",
    "s": "Semimonthly (twice a month)",
    "t": "Three times a year",
    "w": "Weekly",
    "u": "Unknown",
    "z": "Other",
    "": "(not specified)",
}

# ---------------------------------------------------------------------------
# Caption defaults
# ---------------------------------------------------------------------------

DEFAULT_CAPTIONS = {
    "year":  "(year)",
    "month": "(month)",
    "season": "(season)",
}

# What to call an enumeration level the statement did not caption -- the "39"
# of "39 no 1".  By position, because position is all there is to go on.
DEFAULT_ENUM_CAPTIONS = ("v.", "no.", "pt.")


def default_enum_caption(index: int) -> str:
    if index < len(DEFAULT_ENUM_CAPTIONS):
        return DEFAULT_ENUM_CAPTIONS[index]
    return f"level {index + 1}"

# ---------------------------------------------------------------------------
# Subfield conventions
# ---------------------------------------------------------------------------
#
# STANDARD follows MARC 21: $a-$f carry enumeration, $i-$m carry chronology,
# and chronology values are the numeric codes (01-12, 21-24).
#
# HOUSE reproduces the local practice found in existing records, where the year
# occupies $a (an enumeration subfield), enumeration is pushed down to $b/$c,
# and chronology values are written as text ("Mar") rather than codes.  Those
# records are internally inconsistent about the chronology subfield -- 50 use
# $i, one uses $c -- so HOUSE follows the majority and uses $i.

CONVENTION_STANDARD = "standard"
CONVENTION_HOUSE    = "house"

# Enumeration is positional: the first level goes in the first subfield of
# `enum`, the second in the next, and so on.  MARC 21 puts enumeration captions
# in $a-$f in descending order of significance and says nothing about which
# words they hold, so nothing here names a level either.
_SUBFIELD_MAPS: Dict[str, Dict[str, Any]] = {
    CONVENTION_STANDARD: {"enum": ("a", "b", "c", "d", "e", "f"),
                          "year": "i", "month": "j"},
    CONVENTION_HOUSE:    {"enum": ("b", "c", "d", "e", "f"),
                          "year": "a", "month": "i"},
}

# 853 indicators per convention (existing local records use "2"/"0")
_INDICATORS = {
    CONVENTION_STANDARD: ("3", "1"),
    CONVENTION_HOUSE:    ("2", "0"),
}

# The chronology levels a convention can place, in the order they are offered
# to the user.  Enumeration is not in this list: it has no fixed names, only
# positions, and its subfields come from the "enum" sequence above.
CONVENTION_LEVELS = ("year", "month")

# How many enumeration levels the settings dialog offers to move.  Records go
# deeper only rarely, and any level past these keeps the convention's own code.
EDITABLE_ENUM_LEVELS = 3

# The ways a screen may name an enumeration level: by position ("e1", 1), or
# by the words the old three-level model used.  Position is what is stored.
_LEGACY_ENUM_KEYS = {"vol": 0, "issue": 1, "part": 2}

_ORDINALS = ("1st", "2nd", "3rd", "4th", "5th", "6th")


def enum_level_fields(count: int = EDITABLE_ENUM_LEVELS) -> List[Dict[str, str]]:
    """
    The enumeration rows a settings dialog renders, as {key, label}.

    The key is what the API reads back ("e1" is the first level), so the screen
    and resolve_convention name positions the same way.
    """
    return [{"key": f"e{i + 1}",
             "label": f"{_ORDINALS[i]} enumeration",
             "default_caption": default_enum_caption(i)}
            for i in range(count)]


def enum_index(key) -> Optional[int]:
    """The enumeration level a caller's key names, or None if it names none."""
    if isinstance(key, bool):
        return None
    if isinstance(key, int):
        return key if key >= 0 else None
    if isinstance(key, str):
        key = key.strip().lower()
        if key in _LEGACY_ENUM_KEYS:
            return _LEGACY_ENUM_KEYS[key]
        if re.fullmatch(r"e?\d+", key):
            index = int(key.lstrip("e"))
            if key.startswith("e"):        # "e1" is the first level
                index -= 1
            return index if index >= 0 else None
    return None


def enum_subfield(spec_or_map, index: int) -> Optional[str]:
    """The subfield code for enumeration level `index`, or None if too deep."""
    smap = spec_or_map.get("subfields", spec_or_map)
    codes = smap.get("enum", ())
    return codes[index] if index < len(codes) else None

# 853 carries enumeration captions in $a-$h and chronology captions in $i-$m.
# Everything a convention must not touch -- $8 (linking), $u (units per level),
# $v (numbering continuity), $w (frequency), $x/$y/$z -- falls outside a-m, so
# one allowlist covers the whole rule.
_ALLOWED_SUBFIELDS = frozenset("abcdefghijklm")


def convention_presets() -> Dict[str, Dict[str, Any]]:
    """
    The named presets, in the shape the UI populates its fields from.

    Exposed so the template renders from this single source of truth instead of
    duplicating the maps in JavaScript.
    """
    return {
        name: {
            "subfields": dict(_SUBFIELD_MAPS[name]),
            "indicators": list(_INDICATORS[name]),
            "chron_as_text": name == CONVENTION_HOUSE,
        }
        for name in (CONVENTION_STANDARD, CONVENTION_HOUSE)
    }


def resolve_convention(
    name: str = CONVENTION_STANDARD,
    subfields: Optional[Dict[str, str]] = None,
    indicators=None,
    chron_as_text: Optional[bool] = None,
) -> tuple:
    """
    Merge user overrides onto a named preset.

    Returns (spec, rejections) where spec is
    {"subfields": {...}, "indicators": (i1, i2), "chron_as_text": bool}.

    Anything invalid falls back to the preset value and is described in
    `rejections`; a bad subfield code would otherwise corrupt every record it
    touched, silently and identically.
    """
    name = (name or CONVENTION_STANDARD).strip().lower()
    if name not in _SUBFIELD_MAPS:
        name = CONVENTION_STANDARD

    smap = dict(_SUBFIELD_MAPS[name])
    rejections: List[str] = []

    def _in_use(exclude: str) -> set:
        """Every code the map currently spends, ignoring one key."""
        used = set()
        for key, val in smap.items():
            if key == exclude:
                continue
            used.update(val if isinstance(val, tuple) else [val])
        return used

    # A screen written against the old three-level model sends "vol"/"issue"/
    # "part" at the top level; those name enumeration positions now, so fold
    # them into the enumeration patch rather than rejecting them.
    incoming: Dict[str, Any] = {}
    enum_patch: Dict[Any, Any] = {}
    for level, code in (subfields or {}).items():
        if level in smap:
            incoming[level] = code
        elif enum_index(level) is not None:
            enum_patch[level] = code
        else:
            rejections.append(f"Unknown level '{level}' ignored.")
    if enum_patch:
        given = incoming.get("enum")
        if isinstance(given, dict):
            enum_patch.update(given)
        elif given is not None:
            rejections.append(
                "Enumeration was given both as a sequence and by level; "
                "the sequence was used."
            )
            enum_patch = {}
        if enum_patch:
            incoming["enum"] = enum_patch

    # Enumeration is settled first so a chronology override is checked against
    # the codes enumeration ends up with, not the ones it started with.
    for level in sorted(incoming, key=lambda k: k != "enum"):
        code = incoming[level]

        # Enumeration takes a sequence: one code per level, in order.  A screen
        # that only shows the first few levels can send {0: "d"} instead and
        # patch those positions, leaving the convention's depth alone.
        if level == "enum":
            if isinstance(code, dict):
                codes = list(smap["enum"])
                unknown = []
                patched: set = set()
                for key, value in code.items():
                    index = enum_index(key)
                    if index is None or index >= len(codes):
                        unknown.append(str(key))
                        continue
                    codes[index] = str(value or "").strip().lower()
                    patched.add(index)
                if unknown:
                    rejections.append(
                        f"No enumeration level {', '.join(unknown)} in this "
                        f"convention - it has room for {len(smap['enum'])}."
                    )
                # A screen editing the first few levels does not see the ones
                # below, so it cannot know that moving level 1 to $c collides
                # with the level that already held $c.  The levels the
                # cataloguer set win; an untouched level holding a code they
                # claimed drops out, and the depth that costs is reported.
                taken = {codes[i] for i in patched}
                kept = [c for i, c in enumerate(codes)
                        if i in patched or c not in taken]
                if len(kept) != len(codes):
                    rejections.append(
                        f"Enumeration now has room for {len(kept)} levels, not "
                        f"{len(codes)}: a level you did not set was using a "
                        f"subfield you moved another level to."
                    )
                codes = kept
            else:
                codes = [str(c or "").strip().lower()
                         for c in (code if isinstance(code, (list, tuple)) else [code])]
            bad = [c for c in codes if len(c) != 1 or c not in _ALLOWED_SUBFIELDS]
            if bad:
                rejections.append(
                    f"{', '.join(repr(c) for c in bad)} cannot carry an "
                    f"enumeration caption (853 captions live in $a-$m) - kept "
                    f"{''.join('$' + c for c in smap['enum'])}."
                )
                continue
            repeated = sorted({c for c in codes if codes.count(c) > 1})
            if repeated:
                rejections.append(
                    f"{', '.join('$' + c for c in repeated)} would carry two "
                    f"enumeration levels at once - kept "
                    f"{''.join('$' + c for c in smap['enum'])}."
                )
                continue
            clash = sorted(set(codes) & _in_use("enum"))
            if clash:
                rejections.append(
                    f"{', '.join('$' + c for c in clash)} is already used by "
                    f"chronology, so enumeration kept "
                    f"{''.join('$' + c for c in smap['enum'])}."
                )
                continue
            smap["enum"] = tuple(codes)
            continue

        code = (str(code or "")).strip().lower()
        if code == smap[level]:
            continue
        if len(code) != 1 or code not in _ALLOWED_SUBFIELDS:
            rejections.append(
                f"'{code}' is not a usable caption subfield for {level} "
                f"(853 captions live in $a-$m) - kept ${smap[level]}."
            )
            continue
        if code in _in_use(level):
            owner = "enumeration" if code in smap.get("enum", ()) else next(
                (l for l, c in smap.items()
                 if not isinstance(c, tuple) and c == code and l != level), "another level")
            rejections.append(
                f"${code} is already used by {owner}, so {level} kept "
                f"${smap[level]} - two levels cannot share a subfield."
            )
            continue
        smap[level] = code

    ind = _INDICATORS[name]
    if indicators is not None:
        try:
            i1, i2 = (str(x)[:1] if str(x).strip() else " " for x in list(indicators)[:2])
            ind = (i1, i2)
        except (TypeError, ValueError):
            rejections.append(
                f"Indicators {indicators!r} unreadable - kept {_INDICATORS[name]}."
            )

    text = (name == CONVENTION_HOUSE) if chron_as_text is None else bool(chron_as_text)

    return {"subfields": smap, "indicators": ind, "chron_as_text": text}, rejections


# Reverse of MARC_CHRON_CODES for writing chronology as text in HOUSE mode.
_CODE_TO_TEXT: Dict[str, str] = {
    "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May",
    "06": "Jun", "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct",
    "11": "Nov", "12": "Dec",
    "21": "Spring", "22": "Summer", "23": "Fall", "24": "Winter",
}


def _chron_text(value: Optional[str]) -> Optional[str]:
    """Render a chronology value as text ('03' -> 'Mar'), preserving ranges."""
    if value is None:
        return None
    out = []
    for tok in re.split(r"([-/])", value):
        out.append(_CODE_TO_TEXT.get(tok, tok) if tok not in "-/" else tok)
    return "".join(out)


def caption_slot(caption: str) -> Optional[str]:
    """
    What kind of level an 853 caption labels: "year", "month", or "enum".

    Enumeration captions are not named further.  "v.", "no." and "pt." are
    words a cataloguer chose; which level each one *is* comes from the subfield
    it sits in, not from the word.
    """
    c = (caption or "").strip().lower()
    if not c:
        return None
    if "year" in c:
        return "year"
    if "season" in c or "month" in c or "chron" in c:
        return "month"
    # Anything else short enough to be a caption is an enumeration caption.
    # MARC 21 does not restrict the words, and cataloguers use more than three
    # of them -- "Bd.", "Heft", "Report no.", "n.s. v."
    if re.fullmatch(r"\(?[a-z][a-z0-9 .,/'-]{0,23}\)?", c):
        return "enum"
    return None


def _existing_link(existing_853) -> Optional[str]:
    """The $8 linking number carried by an existing 853, if it has one."""
    if existing_853 is None:
        return None
    for sf in getattr(existing_853, "subfields", []):
        if getattr(sf, "code", None) == "8" and getattr(sf, "value", None):
            return sf.value.strip()
    return None


def read_853_slots(existing_853) -> Dict[str, Any]:
    """
    Read an existing 853 into the same shape a convention uses.

    Returns {"enum": (codes in subfield order), "year": code, "month": code},
    omitting what the field does not declare.  Enumeration codes keep the order
    they appear in, because that order *is* the hierarchy.

    Accepts a pymarc Field or any object exposing .subfields with .code/.value.
    Returns {} when nothing recognisable is declared.
    """
    slots: Dict[str, Any] = {}
    enum_codes: List[str] = []
    if existing_853 is None:
        return slots
    for sf in getattr(existing_853, "subfields", []):
        code = getattr(sf, "code", None)
        value = getattr(sf, "value", None)
        if code not in _ALLOWED_SUBFIELDS:
            continue
        level = caption_slot(value)
        if level == "enum":
            if code not in enum_codes:
                enum_codes.append(code)
        elif level and level not in slots:
            slots[level] = code
    if enum_codes:
        slots["enum"] = tuple(enum_codes)
    return slots


def read_853_captions(existing_853) -> List[str]:
    """The enumeration caption words an existing 853 declares, in order."""
    captions: List[str] = []
    for sf in getattr(existing_853, "subfields", []):
        if getattr(sf, "code", None) not in _ALLOWED_SUBFIELDS:
            continue
        value = getattr(sf, "value", None)
        if caption_slot(value) == "enum":
            captions.append(value)
    return captions


def _uses_season_chronology(parse_result: ParseResult) -> bool:
    """True if any parsed month value is a MARC season code (21-24)."""
    for r in parse_result.ranges:
        for ec in (r.start, r.end):
            if ec is None or not ec.month:
                continue
            for token in ec.month.replace("/", "-").split("-"):
                if token.strip() in SEASON_CODES:
                    return True
    return False


# ---------------------------------------------------------------------------
# Data class for the generated field data (serialisable without pymarc)
# ---------------------------------------------------------------------------

@dataclass
class SubfieldData:
    code: str
    value: str


@dataclass
class FieldData:
    """Serialisable representation of a MARC field."""
    tag: str
    indicator1: str
    indicator2: str
    subfields: List[SubfieldData] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tag": self.tag,
            "indicator1": self.indicator1,
            "indicator2": self.indicator2,
            "subfields": [{"code": sf.code, "value": sf.value}
                          for sf in self.subfields],
        }

    def display(self) -> str:
        """Human-readable string like '853 ## $8 1 $a v. $b no. ...'"""
        ind = f"{self.indicator1}{self.indicator2}".replace(" ", "#")
        sf_parts = " ".join(
            f"${sf.code} {sf.value}" for sf in self.subfields
        )
        return f"{self.tag} {ind} {sf_parts}"

    def to_pymarc(self) -> "Field":
        """Convert to a pymarc Field object (requires pymarc installed)."""
        if not HAS_PYMARC:
            raise RuntimeError("pymarc is not installed.")
        subfield_list = []
        for sf in self.subfields:
            subfield_list.append(Subfield(code=sf.code, value=sf.value))
        return Field(
            tag=self.tag,
            indicators=[self.indicator1, self.indicator2],
            subfields=subfield_list,
        )


@dataclass
class ConversionResult:
    """Output of convert_holdings()."""
    field_853: Optional[FieldData]        # None when conforming to an existing 853
    fields_863: List[FieldData]
    linking_number: int                   # the $8 linking number used
    warnings: List[str] = field(default_factory=list)
    conformed: bool = False               # reused the record's existing 853
    needs_review: bool = False            # values found but deliberately not converted
    # Fields were produced, and something about them should be looked at before
    # they are loaded.  Distinct from needs_review, which writes nothing at all:
    # here the record exists and may well be right, but the tool cannot vouch
    # for it, and silence would be read as vouching.
    flagged: bool = False

    def all_fields(self) -> List[FieldData]:
        return ([self.field_853] if self.field_853 else []) + self.fields_863

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_853": self.field_853.to_dict() if self.field_853 else None,
            "fields_863": [f.to_dict() for f in self.fields_863],
            "linking_number": self.linking_number,
            "warnings": self.warnings,
            "conformed": self.conformed,
            "needs_review": self.needs_review,
            "flagged": self.flagged,
        }


# ---------------------------------------------------------------------------
# Caption builder
# ---------------------------------------------------------------------------

def _enum_caption_overrides(captions: Optional[Dict[str, Any]]) -> Dict[int, str]:
    """
    Cataloguer overrides for enumeration captions, keyed by level index.

    Accepts "e1"/"e2"/... and plain integers, and still accepts the old
    "vol"/"issue"/"part" keys as levels 1, 2 and 3 so a screen that has not
    been updated keeps working.
    """
    out: Dict[int, str] = {}
    for key, value in (captions or {}).items():
        if not value:
            continue
        index = enum_index(key)
        if index is not None:
            out[index] = value
    return out


def _build_853(
    parse_result: ParseResult,
    linking_number: int = 1,
    captions: Optional[Dict[str, str]] = None,
    frequency: str = "",
    numbering_continuity: str = "",
    convention: str = CONVENTION_STANDARD,
    convention_spec: Optional[Dict[str, Any]] = None,
    warnings: Optional[List[str]] = None,
) -> FieldData:
    """
    Build an 853 (Captions and Pattern) field from a ParseResult.

    Enumeration captions are written in the order the statements use them, one
    subfield per level, with the word each statement actually used.  A serial
    numbered only by issue gets "$a no." -- correct, and previously impossible.

    Parameters
    ----------
    parse_result      : output of parse_866()
    linking_number    : integer used for $8 (matches 863 $8 prefix)
    captions          : overrides; "e1"/"e2"/... or "year"/"month"
    frequency         : 853 $w code (see FREQUENCY_CODES)
    numbering_continuity : 853 $v -- 'r' (renumbers per level) or 'c' (continuous)
    """
    caps = {**DEFAULT_CAPTIONS, **{k: v for k, v in (captions or {}).items()
                                   if k in DEFAULT_CAPTIONS}}
    enum_caps = _enum_caption_overrides(captions)
    levels = parse_result.caption_union()
    if convention_spec is None:
        convention_spec, _ = resolve_convention(convention)
    smap = convention_spec["subfields"]
    ind1, ind2 = convention_spec["indicators"]

    sfs: List[SubfieldData] = []
    sfs.append(SubfieldData("8", str(linking_number)))

    planned: List[tuple] = []

    declared = levels.get("enum_captions", [])
    for i, caption in enumerate(declared):
        code = enum_subfield(convention_spec, i)
        if code is None:
            if warnings is not None:
                note = (f"This convention has room for {len(smap['enum'])} "
                        f"enumeration levels and the holdings state "
                        f"{len(declared)}; level {i + 1} was not recorded.")
                if note not in warnings:
                    warnings.append(note)
            continue
        planned.append((code, enum_caps.get(i) or caption or default_enum_caption(i)))

    if levels.get("year"):
        planned.append((smap["year"], caps["year"]))
    if levels.get("month"):
        cap = caps["season"] if _uses_season_chronology(parse_result) else caps["month"]
        planned.append((smap["month"], cap))

    # Sorted by subfield so the field reads correctly under either convention
    # (HOUSE puts the year first, in $a).
    last_enum_code = planned[len(declared) - 1][0] if declared else None
    for code, value in sorted(planned, key=lambda p: p[0]):
        sfs.append(SubfieldData(code, value))
        # $v (numbering continuity) belongs to the level that renumbers, which
        # is the last enumeration level.  $u (units per higher level) is never
        # guessed.
        if numbering_continuity and code == last_enum_code:
            sfs.append(SubfieldData("v", numbering_continuity))

    if frequency:
        sfs.append(SubfieldData("w", frequency))

    return FieldData(
        tag="853",
        indicator1=ind1,
        indicator2=ind2,
        subfields=sfs,
    )


# ---------------------------------------------------------------------------
# 863 builder helpers
# ---------------------------------------------------------------------------

# The two level hierarchies an 863 carries, each most significant first.
# Enumeration and chronology are independent: a volume ranging says nothing
# about whether the year does.
_CHRON_LEVELS = ("year", "month")

# Cataloguer-facing names, with their article, for warnings about a level that
# could not be written.
_LEVEL_WORDS = {
    "year":  ("a", "year"),
    "month": ("a", "month or season"),
}


def _enum_label(caption: Optional[str], index: int) -> tuple:
    """Name an enumeration level for a warning, by its caption where there is one."""
    word = (caption or default_enum_caption(index)).strip().rstrip(".")
    article = "an" if word[:1].lower() in "aeiou" else "a"
    return (article, f"{word} level")


# A chronology subfield an 853 labels "(month)" or "(season)" holds MARC codes:
# months 01-12, seasons 21-24, joined by "-" for a range and "/" for a combined
# issue.  Anything else is prose.
_CHRON_CODE = r"(?:0[1-9]|1[0-2]|2[1-4])"
_CHRON_VALUE_RE = re.compile(rf"^{_CHRON_CODE}(?:[-/]{_CHRON_CODE})*-?$")

# A year subfield holds four-digit years, likewise joined.
_YEAR_VALUE_RE = re.compile(r"^\d{4}(?:[-/]\d{4})*-?$")


def _is_codeable(level, value: str) -> bool:
    """Whether `value` may be written into the coded subfield for `level`."""
    if level == "month":
        return bool(_CHRON_VALUE_RE.match(value))
    if level == "year":
        return bool(_YEAR_VALUE_RE.match(value))
    return True


def _note_unplaceable(warnings: Optional[List[str]], which: str,
                      label: tuple, value: str) -> None:
    """
    Record that one boundary states a level the other does not.

    A compressed 863 pairs its subfields positionally, so a value written for
    one end is read as covering both.  With nothing at the other end there is no
    range to express and no notation for half of one, so the level is left out
    -- and named here, which is what keeps it accounted for rather than lost.
    """
    if warnings is None:
        return
    article, word = label
    other = "end" if which == "start" else "start"
    note = (
        f"Only the {which} of this range gives {article} {word} ({value}); a "
        f"compressed 863 records the first and last part held, so with no "
        f"{word} at the {other} it was left out."
    )
    if note not in warnings:
        warnings.append(note)


# How deep a serial is plausibly numbered.  MARC 21 gives enumeration $a-$f, so
# six levels are *allowed*; two or three are what serials actually use --
# volume, issue, and occasionally part.  The 112-statement corpus reaches three
# exactly once and never four.
PLAUSIBLE_ENUM_DEPTH = 3


def _check_enumeration_depth(levels: Dict[str, Any],
                             warnings: Optional[List[str]] = None) -> bool:
    """
    Flag a record claiming more enumeration levels than a serial plausibly has.

    The realistic way to reach four or more is a discontinuous list read as a
    hierarchy: "8,13,15,17,19,20-(1982-1994)" is six separate holdings, and
    filing them as $a 8 $b 13 $c 15 ... states that the library holds one
    issue, numbered six levels deep, which is not a loss but an invention.

    Nothing here can tell the two readings apart -- a genuinely deep serial and
    a list look identical once the values are in hand -- so this does not
    refuse, and does not drop anything. It says what was assumed, which is the
    difference between an error a cataloguer can catch and one they cannot.

    Returns True when the record was flagged.
    """
    captions = levels.get("enum_captions", [])
    if len(captions) <= PLAUSIBLE_ENUM_DEPTH:
        return False
    if warnings is not None:
        named = ", ".join(
            f"{cap or default_enum_caption(i)}" for i, cap in enumerate(captions))
        note = (
            f"{len(captions)} enumeration levels are claimed here ({named}). "
            f"Serials are numbered two or three levels deep; more than that "
            f"usually means a list of separate holdings, and separate holdings "
            f"cannot share one 863. Check this record before loading it."
        )
        if note not in warnings:
            warnings.append(note)
    return True


def _note_caption_conflict(warnings: Optional[List[str]], stated: str,
                           value: str, declared: str) -> None:
    """
    Record a value whose level this record's 853 does not describe.

    One 853 is the caption pattern for every 863 linked to it, so a statement
    numbering by one hierarchy cannot share it with a statement numbering by
    another.  Writing the value anyway would file "v. 6" under "ser.", which
    reads as series 6 and is wrong in a way nothing downstream could detect.
    """
    if warnings is None:
        return
    note = (
        f"'{stated}{value}' was left out: this record's 853 calls that level "
        f"'{declared}', and one 853 has to describe every 863 under it. Split "
        f"the statements that number differently onto their own records."
    )
    if note not in warnings:
        warnings.append(note)


def _note_uncodeable(warnings: Optional[List[str]], label: tuple,
                     value: str) -> None:
    """Record chronology wording the coded subfield cannot hold."""
    if warnings is None:
        return
    _, word = label
    note = (
        f"'{value}' is not something a {word} subfield can hold — it takes "
        f"MARC codes, not wording — so it was left out. Record it by hand if "
        f"it matters."
    )
    if note not in warnings:
        warnings.append(note)


def _hierarchy_values(
    hr: HoldingsRange,
    level_keys,
    get,
    label_for,
    warnings: Optional[List[str]] = None,
) -> Dict[Any, str]:
    """
    The 863 value for every level of one hierarchy, as {key: value}.

    `level_keys` runs most significant first; `get(boundary, key)` reads one
    level off a boundary, and `label_for(key)` names it for a warning.  Two
    hierarchies use this: enumeration, keyed by position, and chronology, keyed
    by "year"/"month".  They are independent -- a volume ranging says nothing
    about whether the year does.

    A compressed 863 records the first part held and the last part held, and a
    reader pairs the subfields positionally: the first value of every subfield
    describes the first part, the second value the last part.  Everything below
    follows from that one fact, and from a single question asked per level --
    *does anything above this level range?*

    No second boundary at all
        A single unit ("v. 58 (Sep 2003)") has nothing to disagree with, so
        every value stands.  An open-ended range writes the trailing hyphen.

    Both ends known, different
        The obvious "41-43".

    Both ends known, equal
        "1-1" when a more significant level ranges, because "$a 41-43 $b 1"
        cannot be read back as v.41:no.1 - v.43:no.1 -- it describes issue 1 of
        each of volumes 41 to 43 just as well.  Plain "1" when nothing above
        ranges: "v. 43 no. 6 - v. 43 no. 7" loses nothing as "$a 43 $b 6-7".  A
        value already containing a range is left alone, since "no. 3-4 - no. 3-4"
        would become the unreadable "3-4-3-4".

    One end only, and the other boundary states nothing at all in this
    hierarchy
        One group is describing the whole range and the parser has hung it on
        whichever boundary carried it.  "v.1:no.1-v.2:no.4(1990-1991)" puts both
        years on the end; "(Jan 1956 - Jan 1957)" puts an already-paired
        "01-01" there.  The value covers both ends and is used as it stands.

    One end only, and nothing above it ranges
        Nothing to pair with, so the value is unambiguous:
        "1983: 5 (7-30 [Jan 28-Dec 29])" states its year once for a run whose
        months range within it.

    One end only, and something above it ranges
        Then the pairing matters and there is no notation for half of it.  The
        "December" in "v. 1 no. 1 (1995)-v. 12 no. 4 (December 2006)" belongs to
        the end alone; writing it asserts the holdings *begin* in December.  The
        "Spring" in "v. 118 no. 1 (Spring 2012)-v. 122 no. 1 (2016)" is the same
        thing pointing the other way.  The level is left out, and a warning names
        the value -- accounted for rather than silently discarded.
    """
    s, e = hr.start, hr.end
    oe = hr.open_ended

    # Whether each boundary was written out at all at this hierarchy.  A value
    # found on one boundary while the other is silent throughout came from a
    # group covering the whole range, not from one end of it -- which is what
    # separates the "01-01" of "(Jan 1956 - Jan 1957)", already a pair, from the
    # "1-2" of "v. 1 (1956) - v. 51 nos. 1-2 (2006)", which is a range inside
    # the end boundary and says nothing about where the run starts.
    speaks = {
        "start": any(get(s, key) is not None for key in level_keys),
        "end": e is not None and any(
            get(e, key) is not None for key in level_keys
        ),
    }

    out: Dict[str, str] = {}
    ranged_above = False

    for key in level_keys:
        s_val = get(s, key)
        e_val = get(e, key) if e is not None else None
        value: Optional[str] = None

        if e is None:
            # Single unit: no other end to disagree with.
            if s_val is not None:
                value = f"{s_val}-" if oe else s_val
        elif s_val is not None and e_val is not None:
            if s_val != e_val:
                value = f"{s_val}-{e_val}"
            elif ranged_above and "-" not in s_val:
                value = f"{s_val}-{s_val}"
            else:
                value = s_val
        elif s_val is not None or e_val is not None:
            lone = s_val if s_val is not None else e_val
            which = "start" if s_val is not None else "end"
            other = "end" if which == "start" else "start"
            if speaks[other] and ranged_above:
                _note_unplaceable(warnings, which, label_for(key), lone)
            else:
                value = lone

        if value and not _is_codeable(key, value):
            # "(1998 Buyers Guide)" is a named issue, not a date, and
            # "Late Summer" is not a season MARC has a code for.  Both used to
            # reach $j, which the 853 declares as "(month)" -- and
            # "11/12-Late Summer" put codes and prose in one subfield.  The
            # record cannot carry it, so it is left out and named.
            _note_uncodeable(warnings, label_for(key), value)
            value = None

        if value:
            out[key] = value
            # A written value that is itself a range is what makes the levels
            # under it need both of their endpoints. Reading it back off the
            # output covers every branch above at once, including the one where
            # the range arrived pre-compressed from the parser ("1990-1991").
            if "-" in value.rstrip("-"):
                ranged_above = True

    return out


def _build_863_for_range(
    hr: HoldingsRange,
    linking_number: int,
    sequence: int,
    levels: dict,
    smap: Optional[Dict[str, str]] = None,
    chron_as_text: bool = False,
    warnings: Optional[List[str]] = None,
) -> FieldData:
    """
    Build a single 863 field for one HoldingsRange.

    `smap` maps levels to subfield codes -- an ordered "enum" sequence plus the
    chronology codes -- so the 863 lands in the same subfields the governing 853
    declares.  `chron_as_text` writes chronology as
    text ("Mar") instead of MARC codes ("03").  `warnings`, when given, collects
    notes about values the range states that could not be encoded -- see
    _hierarchy_values, which decides what those are.
    """
    smap = smap or _SUBFIELD_MAPS[CONVENTION_STANDARD]

    sfs: List[SubfieldData] = []
    sfs.append(SubfieldData("8", f"{linking_number}.{sequence}"))

    depth = len(levels.get("enum_captions", []))
    captions = levels.get("enum_captions", [])

    planned: List[tuple] = []

    enum_values = _hierarchy_values(
        hr, range(depth),
        lambda ec, i: ec.value_at(i) if ec else None,
        lambda i: _enum_label(captions[i] if i < len(captions) else None, i),
        warnings,
    )
    stated = hr.enum_captions()
    for i in range(depth):
        code = enum_subfield(smap, i)
        value = enum_values.get(i)
        if not (code and value):
            continue
        # One 853 governs every 863 linked to it, so a level's caption is the
        # same for all of them.  A range that calls this level something else
        # is describing a different hierarchy, and writing its value here would
        # file it under a caption the statement contradicts.
        mine = stated[i] if i < len(stated) else None
        theirs = captions[i] if i < len(captions) else None
        if mine and theirs and mine != theirs:
            _note_caption_conflict(warnings, mine, value, theirs)
            continue
        planned.append((code, value))

    chron_values = _hierarchy_values(
        hr, _CHRON_LEVELS,
        lambda ec, name: getattr(ec, name) if ec else None,
        lambda name: _LEVEL_WORDS[name],
        warnings,
    )
    for name in _CHRON_LEVELS:
        if not levels.get(name):
            continue
        value = chron_values.get(name)
        if not value:
            continue
        if name == "month" and chron_as_text:
            value = _chron_text(value)
        planned.append((smap[name], value))

    for code, value in sorted(planned, key=lambda p: p[0]):
        sfs.append(SubfieldData(code, value))

    # Indicator 1 is Field encoding level, matching Leader/17: 3, 4 or 5.  4 is
    # holdings level 4 -- enumeration and chronology recorded -- which is what
    # this field carries.
    #
    # Indicator 2 is Form of holdings: 0 compressed, 1 uncompressed, 2 and 3 the
    # same pair where the display comes from a linked 866.  Every field built
    # here states the first part held and the last part held as a range
    # ("$a 41-43 $i 1984-1986"), which is the definition of compressed, so it is
    # 0.  It was 1 -- uncompressed, meaning each part itemised separately --
    # which said the opposite of what the field contains.
    return FieldData(
        tag="863",
        indicator1="4",  # field encoding level 4: enumeration and chronology
        indicator2="0",  # form of holdings: compressed
        subfields=sfs,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert_holdings(
    parse_result: ParseResult,
    linking_number: int = 1,
    captions: Optional[Dict[str, str]] = None,
    frequency: str = "",
    numbering_continuity: str = "",
    existing_853=None,
    convention: str = CONVENTION_STANDARD,
    chron_as_text: bool = False,
    convention_spec: Optional[Dict[str, Any]] = None,
) -> ConversionResult:
    """
    Convert a ParseResult into 853 + 863 MARC field data.

    Parameters
    ----------
    parse_result         : output of holdings_parser.parse_866()
    linking_number       : integer $8 linking number (1, 2, ...)
    captions             : caption overrides (keys: vol, issue, part, year, month)
    frequency            : 853 $w code
    numbering_continuity : 853 $v ('r' or 'c')
    existing_853         : the record's current 853, if it has one.  When it
                           declares a slot for every level found in the data,
                           only 863s are produced and field_853 is None so the
                           caller does not add a second, conflicting 853.
    convention           : CONVENTION_STANDARD or CONVENTION_HOUSE - the preset
                           a *regenerated* 853 starts from
    chron_as_text        : write chronology as text ("Mar") rather than MARC
                           codes ("03"), matching local practice
    convention_spec      : a fully-resolved spec from resolve_convention(),
                           letting the cataloger define the convention rather
                           than inherit a preset.  Overrides the two arguments
                           above.  Ignored when conforming to an existing 853,
                           whose own declared subfields always win.

    Returns
    -------
    ConversionResult; field_853 is None when conforming to an existing 853 or
    when the statement was withheld for review.
    """
    warnings = list(parse_result.warnings)
    levels = parse_result.caption_union()

    # A caller may pass a fully-resolved spec (from the UI) or just a preset
    # name; resolving here keeps every existing call site working unchanged.
    if convention_spec is None:
        convention_spec, _ = resolve_convention(
            convention, chron_as_text=chron_as_text
        )
    chron_as_text = convention_spec["chron_as_text"]

    # Nothing was parsed, or the parser deliberately withheld a value because
    # its level could not be determined.  Emit no fields.
    if parse_result.needs_review or not parse_result.ranges:
        return ConversionResult(
            field_853=None,
            fields_863=[],
            linking_number=linking_number,
            warnings=warnings,
            needs_review=parse_result.needs_review,
        )

    # ── Conform to the record's own 853 when it covers every level found ──
    # An existing 853 covers the data when it declares at least as many
    # enumeration levels as the statements use, and a subfield for every
    # chronology level they use.  Depth is the test for enumeration, because
    # position is what an enumeration caption means.
    declared = read_853_slots(existing_853)
    depth_needed = len(levels.get("enum_captions", []))
    depth_declared = len(declared.get("enum", ()))
    chron_needed = {lvl for lvl in _CHRON_LEVELS if levels.get(lvl)}
    chron_declared = {lvl for lvl in _CHRON_LEVELS if lvl in declared}
    covers = (depth_declared >= depth_needed
              and chron_needed <= chron_declared)

    if declared and covers:
        # The 863s belong to the existing 853, so they must carry *its* $8 —
        # not this statement's position in the record.
        link = _existing_link(existing_853) or linking_number
        fields_863 = [
            _build_863_for_range(hr, link, seq, levels, smap=declared,
                                 chron_as_text=chron_as_text, warnings=warnings)
            for seq, hr in enumerate(parse_result.ranges, start=1)
        ]
        return ConversionResult(
            field_853=None,           # the existing one governs; do not add another
            fields_863=fields_863,
            linking_number=link,
            warnings=warnings,
            conformed=True,
            flagged=_check_enumeration_depth(levels, warnings),
        )

    if declared:
        missing = sorted(chron_needed - chron_declared)
        if depth_declared < depth_needed:
            missing.append(
                f"{depth_needed} enumeration levels (it declares "
                f"{depth_declared})"
            )
        warnings.append(
            "The existing 853 declares no level for "
            f"{', '.join(missing)} but the 866 contains "
            f"{'them' if len(missing) > 1 else 'one'} — "
            "regenerated a complete 853 from the data."
        )

    # ── Regenerate a complete 853 in the requested convention ──
    smap = convention_spec["subfields"]

    field_853 = _build_853(
        parse_result,
        linking_number=linking_number,
        captions=captions,
        frequency=frequency,
        numbering_continuity=numbering_continuity,
        convention_spec=convention_spec,
        warnings=warnings,
    )

    fields_863: List[FieldData] = []
    for seq, hr in enumerate(parse_result.ranges, start=1):
        f863 = _build_863_for_range(hr, linking_number, seq, levels, smap=smap,
                                    chron_as_text=chron_as_text, warnings=warnings)
        fields_863.append(f863)

    return ConversionResult(
        field_853=field_853,
        fields_863=fields_863,
        linking_number=linking_number,
        warnings=warnings,
        flagged=_check_enumeration_depth(levels, warnings),
    )


@dataclass
class RecordConversion:
    """Output of convert_record() -- everything one record needs written."""
    fields_853: List[FieldData] = field(default_factory=list)
    fields_863: List[FieldData] = field(default_factory=list)
    links_written: List[str] = field(default_factory=list)
    results: List[ConversionResult] = field(default_factory=list)  # per statement
    # Links whose run merged statements recording different amounts of detail.
    # The cataloguer may disagree that those are one publication, so the screen
    # marks them rather than presenting the merge as a finding.
    merged_links: List[str] = field(default_factory=list)

    @property
    def needs_review(self) -> int:
        return sum(1 for r in self.results if r.needs_review)

    @property
    def converted(self) -> int:
        return sum(1 for r in self.results if r.fields_863)

    @property
    def conformed(self) -> int:
        return sum(1 for r in self.results if r.conformed)

    @property
    def warnings(self) -> List[str]:
        seen, out = set(), []
        for r in self.results:
            for w in r.warnings:
                if w not in seen:
                    seen.add(w)
                    out.append(w)
        return out


def _set_subfield(field_data: FieldData, code: str, value: str) -> None:
    """Overwrite a subfield's value in place (used to stamp the final $8)."""
    for sf in field_data.subfields:
        if sf.code == code:
            sf.value = value
            return


def _pattern_key(field_853: FieldData) -> tuple:
    """The publication pattern an 853 expresses, ignoring its linking number."""
    return tuple((sf.code, sf.value) for sf in field_853.subfields if sf.code != "8")


def _pattern_map(field_853: FieldData) -> Dict[str, str]:
    """The same thing as a mapping, for comparing two patterns subfield by subfield."""
    return {sf.code: sf.value for sf in field_853.subfields if sf.code != "8"}


def _same_publication_pattern(a: Dict[str, str], b: Dict[str, str]) -> bool:
    """
    Whether two 853s describe one publication pattern rather than two.

    They do when every caption they both carry agrees, and one carries a subset
    of the other's.  A statement recording less detail than its neighbour --
    "v.5(1994)" beside "v.1:no.1(1990)" -- is the same publication with the
    issue simply not recorded, and an 863 need not fill every caption its 853
    declares.

    Carrying the *same* caption with a *different* value is a real change and
    never merges: month chronology and season chronology both use $j, so
    "$j (month)" and "$j (season)" disagree on a shared caption and stay apart.
    That distinction is the whole reason this is a subset test rather than an
    intersection one.
    """
    shared = a.keys() & b.keys()
    if any(a[k] != b[k] for k in shared):
        return False
    return a.keys() <= b.keys() or b.keys() <= a.keys()


def convert_record(
    parse_results: List[ParseResult],
    existing_853s: Optional[List] = None,
    captions: Optional[Dict[str, str]] = None,
    frequency: str = "",
    numbering_continuity: str = "",
    convention_spec: Optional[Dict[str, Any]] = None,
    merge_patterns: bool = True,
) -> RecordConversion:
    """
    Convert every 866 statement on one record, sharing 853s across statements
    that express the same publication pattern.

    MARC 21 treats the 853 as a caption *pattern*: a gap in holdings is another
    863 under the same 853, not a new one.  Numbering therefore cannot be
    decided per statement -- convert_holdings() cannot see its siblings -- so
    this function assigns every $8 once the whole record is known.

    Statements are grouped into *runs*: consecutive statements describing one
    publication pattern share a linking number and receive consecutive 863
    sequence numbers.  A pattern that returns after a different one has
    intervened starts a new run and takes the next linking number, because the
    publication changed twice and the record should say so:

        v. 1 no. 1-4 (Mar-Dec 2001)     months    $8 1
        v. 2 no. 1-4 (Winter-Fall 2002) seasons   $8 2
        v. 3 no. 1-4 (Mar-Dec 2003)     months    $8 3   <- not 1

    This means two identical 853s can be generated, differing only in $8.  That
    is deliberate and is not the fault v0.5.0 removed: that was one 853 per
    *statement* even where nothing had changed, whereas these mark genuinely
    separate runs either side of a change.

    Grouping therefore depends on 866 field order, which carries meaning -- a
    record whose 866s are not in publication order will produce more runs than
    it should.  A statement held for review does not break a run: nothing is
    known about it, so it is no evidence of a change.

    Statements conforming to an 853 already on the record adopt its $8 and are
    grouped by that field rather than by run, since it is one field already on
    the record and cannot be duplicated.  Any link number written to is
    reported in `links_written` so the caller can drop superseded 863s.

    `merge_patterns` False requires runs to agree exactly, so statements
    recording different amounts of detail stay apart.  Whether "v.5(1994)" beside
    "v.1:no.1(1990)" is one publication or two is a judgement about the serial,
    not about the strings, so a cataloguer who knows it is two can say so.
    """

    existing = list(existing_853s or [])
    out = RecordConversion()
    _compatible = (_same_publication_pattern if merge_patterns
                   else (lambda a, b: a == b))

    # Runs, in the order they open.  Each carries the members that share its
    # 853, the pattern they agree on, and the member whose 853 is the fullest --
    # that is the one field the record gets, so a run merged from a sparser
    # statement and a richer one is described by the richer.
    groups: "List[Dict[str, Any]]" = []
    by_link: "Dict[str, Dict[str, Any]]" = {}
    open_group: "Optional[Dict[str, Any]]" = None

    for pr in parse_results:
        # Pick the existing 853 this statement could conform to, if any.
        best = None
        for cand in existing:
            probe = convert_holdings(
                pr, existing_853=cand, captions=captions, frequency=frequency,
                numbering_continuity=numbering_continuity,
                convention_spec=convention_spec,
            )
            if probe.conformed:
                best = probe
                break

        cr = best or convert_holdings(
            pr, captions=captions, frequency=frequency,
            numbering_continuity=numbering_continuity,
            convention_spec=convention_spec,
        )
        out.results.append(cr)

        if cr.needs_review or not cr.fields_863:
            continue

        if cr.conformed:
            # An 853 already on the record: one field, so one group, however
            # many times statements return to it.
            link = str(cr.linking_number)
            group = by_link.get(link)
            if group is None:
                group = {"link": link, "pattern": None, "members": [], "head": cr}
                groups.append(group)
                by_link[link] = group
        elif (open_group is not None and open_group["link"] is None
              and _compatible(open_group["pattern"], _pattern_map(cr.field_853))):
            group = open_group
            pattern = _pattern_map(cr.field_853)
            if pattern != group["pattern"]:
                # Joined on a subset rather than an exact match: worth marking,
                # since it is the one grouping decision a cataloguer might not
                # agree with.
                group["merged"] = True
            if len(pattern) > len(group["pattern"]):
                # A later statement records more: the run is described by the
                # fuller 853, and the sparser 863s simply omit what they lack.
                group["pattern"] = pattern
                group["head"] = cr
        else:
            group = {"link": None, "pattern": _pattern_map(cr.field_853),
                     "members": [], "head": cr}
            groups.append(group)

        group["members"].append(cr)
        open_group = group

    # Allocate link numbers in run order, stepping around any a conformed group
    # already owns.
    taken = {g["link"] for g in groups if g["link"]}
    nxt = 1
    for group in groups:
        if group["link"]:
            continue
        while str(nxt) in taken:
            nxt += 1
        group["link"] = str(nxt)
        taken.add(str(nxt))
        nxt += 1

    # Stamp $8 and let 863 sequence numbers run across the whole group.
    for group in groups:
        link = group["link"]
        out.links_written.append(link)
        if group.get("merged"):
            out.merged_links.append(link)
        seq = 1
        for cr in group["members"]:
            for f863 in cr.fields_863:
                _set_subfield(f863, "8", f"{link}.{seq}")
                out.fields_863.append(f863)
                seq += 1
            cr.linking_number = link
            # Every member reports the run's 853, not the one it would have had
            # alone. Only the head's field reaches the record, so a member
            # showing its own would be previewing a field that is never written
            # -- visible whenever a run merged a sparser statement with a
            # fuller one.
            if cr.field_853 is not None and group["head"].field_853 is not None:
                cr.field_853 = group["head"].field_853
            if cr.field_853 is not None:
                _set_subfield(cr.field_853, "8", link)
        # One 853 per run, the fullest of its members; conformed groups already
        # have their field on the record.
        head = group["head"]
        if head.field_853 is not None:
            out.fields_853.append(head.field_853)

    return out


def apply_to_record(
    record: "Record",
    conversion: ConversionResult,
    remove_866: bool = True,
    original_866_tag: Optional[str] = None,
) -> "Record":
    """
    Apply a ConversionResult to a pymarc Record in-place.

    Adds the 853/863 fields and optionally removes the source 866.

    Parameters
    ----------
    record           : pymarc Record to modify
    conversion       : output of convert_holdings()
    remove_866       : if True, remove matching 866 fields
    original_866_tag : specific 866 field tag to remove (for future use)
    """
    if not HAS_PYMARC:
        raise RuntimeError("pymarc must be installed to work with Record objects.")

    # Add 853
    record.add_field(conversion.field_853.to_pymarc())

    # Add 863s
    for f863 in conversion.fields_863:
        record.add_field(f863.to_pymarc())

    # Optionally strip source 866 fields
    if remove_866:
        record.remove_fields("866")

    return record
