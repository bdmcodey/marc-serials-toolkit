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

from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

try:
    from pymarc import Field, Subfield, Record
    HAS_PYMARC = True
except ImportError:
    HAS_PYMARC = False

from holdings_parser import ParseResult, HoldingsRange, EnumChron, SEASON_CODES


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
    field_853: FieldData
    fields_863: List[FieldData]
    linking_number: int          # the $8 linking number used
    warnings: List[str] = field(default_factory=list)

    def all_fields(self) -> List[FieldData]:
        return [self.field_853] + self.fields_863

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_853": self.field_853.to_dict(),
            "fields_863": [f.to_dict() for f in self.fields_863],
            "linking_number": self.linking_number,
            "warnings": self.warnings,
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

    sfs: List[SubfieldData] = []
    sfs.append(SubfieldData("8", str(linking_number)))

    # Enumeration captions
    if levels.get("vol"):
        sfs.append(SubfieldData("a", caps["vol"]))
    if levels.get("issue"):
        sfs.append(SubfieldData("b", caps["issue"]))
        # $v (numbering continuity) only when the cataloger supplies it;
        # $u (units per higher level) is never guessed.
        if numbering_continuity:
            sfs.append(SubfieldData("v", numbering_continuity))
    if levels.get("part"):
        sfs.append(SubfieldData("c", caps["part"]))

    # Chronology captions
    if levels.get("year"):
        sfs.append(SubfieldData("i", caps["year"]))
    if levels.get("month"):
        cap = caps["season"] if _uses_season_chronology(parse_result) else caps["month"]
        sfs.append(SubfieldData("j", cap))

    # Frequency
    if frequency:
        sfs.append(SubfieldData("w", frequency))

    return FieldData(
        tag="853",
        indicator1="3",  # 3 = holds compressed captions/patterns
        indicator2="1",  # 1 = not compressed
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
) -> FieldData:
    """Build a single 863 field for one HoldingsRange."""
    s = hr.start
    e = hr.end  # may be None (open-ended)
    oe = hr.open_ended

    sfs: List[SubfieldData] = []
    sfs.append(SubfieldData("8", f"{linking_number}.{sequence}"))

    # Enumeration
    if levels.get("vol"):
        val = _enum_value(s.vol, e.vol if e else None, oe)
        if val:
            sfs.append(SubfieldData("a", val))

    if levels.get("issue"):
        val = _enum_value(s.issue, e.issue if e else None, oe)
        if val:
            sfs.append(SubfieldData("b", val))

    if levels.get("part"):
        val = _enum_value(s.part, e.part if e else None, oe)
        if val:
            sfs.append(SubfieldData("c", val))

    # Chronology.  In "chron at end only" patterns such as
    # "v.1:no.1-v.2:no.4(1990-1991)" the single chronology group covers
    # the whole range, so fall back to the end boundary's values.
    if levels.get("year"):
        start_year = s.year if s.year is not None else (e.year if e else None)
        end_year = e.year if (e and s.year is not None) else None
        val = _chron_value(start_year, end_year, oe)
        if val:
            sfs.append(SubfieldData("i", val))

    if levels.get("month"):
        start_month = s.month if s.month is not None else (e.month if e else None)
        end_month = e.month if (e and s.month is not None) else None
        val = _chron_value(start_month, end_month, oe)
        if val:
            sfs.append(SubfieldData("j", val))

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
) -> ConversionResult:
    """
    Convert a ParseResult into 853 + 863 MARC field data.

    Parameters
    ----------
    parse_result         : output of holdings_parser.parse_866()
    linking_number       : integer $8 linking number (1, 2, …)
    captions             : caption overrides (keys: vol, issue, part, year, month)
    frequency            : 853 $w code
    numbering_continuity : 853 $v ('r' or 'c')

    Returns
    -------
    ConversionResult with field_853 and fields_863 lists
    """
    warnings = list(parse_result.warnings)
    levels = parse_result.caption_union()

    field_853 = _build_853(
        parse_result,
        linking_number=linking_number,
        captions=captions,
        frequency=frequency,
        numbering_continuity=numbering_continuity,
    )

    fields_863: List[FieldData] = []
    for seq, hr in enumerate(parse_result.ranges, start=1):
        f863 = _build_863_for_range(hr, linking_number, seq, levels)
        fields_863.append(f863)

    return ConversionResult(
        field_853=field_853,
        fields_863=fields_863,
        linking_number=linking_number,
        warnings=warnings,
    )


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
	