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
    "vol":   "v.",
    "issue": "no.",
    "part":  "pt.",
    "year":  "(year)",
    "month": "(month)",
    "season": "(season)",
}

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

_SUBFIELD_MAPS: Dict[str, Dict[str, str]] = {
    CONVENTION_STANDARD: {"vol": "a", "issue": "b", "part": "c",
                          "year": "i", "month": "j"},
    CONVENTION_HOUSE:    {"year": "a", "vol": "b", "issue": "c",
                          "part": "d", "month": "i"},
}

# 853 indicators per convention (existing local records use "2"/"0")
_INDICATORS = {
    CONVENTION_STANDARD: ("3", "1"),
    CONVENTION_HOUSE:    ("2", "0"),
}

# The levels a convention can place, in the order they are offered to the user.
CONVENTION_LEVELS = ("vol", "issue", "part", "year", "month")

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

    for level, code in (subfields or {}).items():
        if level not in smap:
            rejections.append(f"Unknown level '{level}' ignored.")
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
        clash = next((l for l, c in smap.items() if c == code and l != level), None)
        if clash:
            rejections.append(
                f"${code} is already used by {clash}, so {level} kept "
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
    Map an 853 caption string to the semantic level it labels.

    "(year)" -> "year",  "(season)"/"(month)" -> "month",
    "v."     -> "vol",   "no."                -> "issue",  "pt." -> "part"
    """
    c = (caption or "").strip().lower()
    if not c:
        return None
    if "year" in c:
        return "year"
    if "season" in c or "month" in c or "chron" in c:
        return "month"
    if re.match(r"^\(?v(ol(ume)?)?\.?\)?$", c):
        return "vol"
    if re.match(r"^\(?(nos?|nr|num(ber)?s?|iss(ue)?s?)\.?\)?$", c):
        return "issue"
    if re.match(r"^\(?(pt|part)s?\.?\)?$", c):
        return "part"
    return None


def _existing_link(existing_853) -> Optional[str]:
    """The $8 linking number carried by an existing 853, if it has one."""
    if existing_853 is None:
        return None
    for sf in getattr(existing_853, "subfields", []):
        if getattr(sf, "code", None) == "8" and getattr(sf, "value", None):
            return sf.value.strip()
    return None


def read_853_slots(existing_853) -> Dict[str, str]:
    """
    Read an existing 853 into {semantic level: subfield code}.

    Accepts a pymarc Field or any object exposing .subfields with .code/.value.
    Returns {} when nothing recognisable is declared.
    """
    slots: Dict[str, str] = {}
    if existing_853 is None:
        return slots
    for sf in getattr(existing_853, "subfields", []):
        code = getattr(sf, "code", None)
        value = getattr(sf, "value", None)
        if code is None or code == "8":
            continue
        level = caption_slot(value)
        if level and level not in slots:
            slots[level] = code
    return slots


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
        }


# ---------------------------------------------------------------------------
# Caption builder
# ---------------------------------------------------------------------------

def _build_853(
    parse_result: ParseResult,
    linking_number: int = 1,
    captions: Optional[Dict[str, str]] = None,
    frequency: str = "",
    numbering_continuity: str = "",
    convention: str = CONVENTION_STANDARD,
    convention_spec: Optional[Dict[str, Any]] = None,
) -> FieldData:
    """
    Build an 853 (Captions and Pattern) field from a ParseResult.

    Parameters
    ----------
    parse_result      : output of parse_866()
    linking_number    : integer used for $8 (matches 863 $8 prefix)
    captions          : override caption strings; keys: vol, issue, part, year, month
    frequency         : 853 $w code (see FREQUENCY_CODES)
    numbering_continuity : 853 $v – 'r' (renumbers per volume) or 'c' (continuous)
    """
    caps = {**DEFAULT_CAPTIONS, **(captions or {})}
    levels = parse_result.caption_union()
    if convention_spec is None:
        convention_spec, _ = resolve_convention(convention)
    smap = convention_spec["subfields"]
    ind1, ind2 = convention_spec["indicators"]

    sfs: List[SubfieldData] = []
    sfs.append(SubfieldData("8", str(linking_number)))

    # Emit captions in subfield order so the field reads correctly under
    # either convention (HOUSE puts the year first, in $a).
    planned: List[tuple] = []
    if levels.get("vol"):
        planned.append((smap["vol"], caps["vol"], "vol"))
    if levels.get("issue"):
        planned.append((smap["issue"], caps["issue"], "issue"))
    if levels.get("part"):
        planned.append((smap["part"], caps["part"], "part"))
    if levels.get("year"):
        planned.append((smap["year"], caps["year"], "year"))
    if levels.get("month"):
        cap = caps["season"] if _uses_season_chronology(parse_result) else caps["month"]
        planned.append((smap["month"], cap, "month"))

    for code, value, level in sorted(planned, key=lambda p: p[0]):
        sfs.append(SubfieldData(code, value))
        # $v (numbering continuity) only when the cataloger supplies it;
        # $u (units per higher level) is never guessed.
        if level == "issue" and numbering_continuity:
            sfs.append(SubfieldData("v", numbering_continuity))

    # Frequency
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

def _enum_value(start: Optional[str], end: Optional[str],
                open_ended: bool) -> Optional[str]:
    """
    Produce the subfield value for an enumeration level:
      single item → "3"
      closed range → "3-7"
      open range  → "3-"
    """
    if start is None:
        return None
    if end is None and not open_ended:
        return start
    if open_ended:
        return f"{start}-"
    return f"{start}-{end}" if end != start else start


def _chron_value(start: Optional[str], end: Optional[str],
                 open_ended: bool) -> Optional[str]:
    """Same as _enum_value but for chronology strings."""
    if start is None:
        return None
    if end is None and not open_ended:
        return start
    if open_ended:
        return f"{start}-"
    return f"{start}-{end}" if end != start else start


def _build_863_for_range(
    hr: HoldingsRange,
    linking_number: int,
    sequence: int,
    levels: dict,
    smap: Optional[Dict[str, str]] = None,
    chron_as_text: bool = False,
) -> FieldData:
    """
    Build a single 863 field for one HoldingsRange.

    `smap` maps semantic levels to subfield codes, so the 863 lands in the same
    subfields the governing 853 declares.  `chron_as_text` writes chronology as
    text ("Mar") instead of MARC codes ("03").
    """
    smap = smap or _SUBFIELD_MAPS[CONVENTION_STANDARD]
    s = hr.start
    e = hr.end  # may be None (open-ended)
    oe = hr.open_ended

    sfs: List[SubfieldData] = []
    sfs.append(SubfieldData("8", f"{linking_number}.{sequence}"))

    planned: List[tuple] = []

    # Enumeration
    if levels.get("vol"):
        val = _enum_value(s.vol, e.vol if e else None, oe)
        if val:
            planned.append((smap["vol"], val))

    if levels.get("issue"):
        val = _enum_value(s.issue, e.issue if e else None, oe)
        if val:
            planned.append((smap["issue"], val))

    if levels.get("part"):
        val = _enum_value(s.part, e.part if e else None, oe)
        if val:
            planned.append((smap["part"], val))

    # Chronology.  In "chron at end only" patterns such as
    # "v.1:no.1-v.2:no.4(1990-1991)" the single chronology group covers
    # the whole range, so fall back to the end boundary's values.
    if levels.get("year"):
        start_year = s.year if s.year is not None else (e.year if e else None)
        end_year = e.year if (e and s.year is not None) else None
        val = _chron_value(start_year, end_year, oe)
        if val:
            planned.append((smap["year"], val))

    if levels.get("month"):
        start_month = s.month if s.month is not None else (e.month if e else None)
        end_month = e.month if (e and s.month is not None) else None
        val = _chron_value(start_month, end_month, oe)
        if val:
            planned.append((smap["month"], _chron_text(val) if chron_as_text else val))

    for code, value in sorted(planned, key=lambda p: p[0]):
        sfs.append(SubfieldData(code, value))

    return FieldData(
        tag="863",
        indicator1="4",  # 4 = no information provided / n/a
        indicator2="1",  # 1 = compressed using / range designation
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
    declared = read_853_slots(existing_853)
    needed = {lvl for lvl in ("vol", "issue", "part", "year", "month")
              if levels.get(lvl)}

    if declared and needed <= set(declared):
        # The 863s belong to the existing 853, so they must carry *its* $8 —
        # not this statement's position in the record.
        link = _existing_link(existing_853) or linking_number
        fields_863 = [
            _build_863_for_range(hr, link, seq, levels,
                                 smap=declared, chron_as_text=chron_as_text)
            for seq, hr in enumerate(parse_result.ranges, start=1)
        ]
        return ConversionResult(
            field_853=None,           # the existing one governs; do not add another
            fields_863=fields_863,
            linking_number=link,
            warnings=warnings,
            conformed=True,
        )

    if declared:
        missing = sorted(needed - set(declared))
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
    )

    fields_863: List[FieldData] = []
    for seq, hr in enumerate(parse_result.ranges, start=1):
        f863 = _build_863_for_range(hr, linking_number, seq, levels,
                                    smap=smap, chron_as_text=chron_as_text)
        fields_863.append(f863)

    return ConversionResult(
        field_853=field_853,
        fields_863=fields_863,
        linking_number=linking_number,
        warnings=warnings,
    )


@dataclass
class RecordConversion:
    """Output of convert_record() -- everything one record needs written."""
    fields_853: List[FieldData] = field(default_factory=list)
    fields_863: List[FieldData] = field(default_factory=list)
    links_written: List[str] = field(default_factory=list)
    results: List[ConversionResult] = field(default_factory=list)  # per statement

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
    """
    existing = list(existing_853s or [])
    out = RecordConversion()

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
              and _same_publication_pattern(open_group["pattern"],
                                            _pattern_map(cr.field_853))):
            group = open_group
            pattern = _pattern_map(cr.field_853)
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
