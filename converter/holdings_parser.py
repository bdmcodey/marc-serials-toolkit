"""
holdings_parser.py
------------------
Parses textual MARC 866 holdings statements into structured data
that can be used to generate 853 (caption/pattern) and 863
(enumeration/chronology) MARC fields.

Supported 866 $a patterns (case-insensitive):
  v.1(1990)-v.5(1994)
  v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)
  v.1:no.1(1990:Spring)-v.5:no.4(1994:Winter)
  v.6(1995)-                              ← open-ended / current
  Vol. 1, No. 1 (Spring 1990)-...
  1990-1994                               ← year-only holdings
  v.1-5(1990-1994)                        ← compressed range format
  v.1:no.1-v.2:no.4(1990-1991)           ← chron at end only
  v.8 no.3-v.10 no.2(1981-Fall 1983)     ← trailing chron range w/ season
  v.27 no.4-v.31 no.4(April 1992-April 1996)  ← trailing Mon YYYY range
  34 no 3(Summer 1990)                    ← bare volume number (no caption)
  Multiple ranges: "v.1(1990)-v.3(1992), v.5(1994)-"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

# ---------------------------------------------------------------------------
# Month / season normalisation
# ---------------------------------------------------------------------------

MONTH_MAP: dict[str, str] = {
    "jan": "Jan.", "feb": "Feb.", "mar": "Mar.", "apr": "Apr.",
    "may": "May", "jun": "Jun.", "jul": "Jul.", "aug": "Aug.",
    "sep": "Sep.", "oct": "Oct.", "nov": "Nov.", "dec": "Dec.",
    # Long forms
    "january": "Jan.", "february": "Feb.", "march": "Mar.",
    "april": "Apr.", "june": "Jun.", "july": "Jul.",
    "august": "Aug.", "september": "Sep.", "october": "Oct.",
    "november": "Nov.", "december": "Dec.",
}

SEASON_MAP: dict[str, str] = {
    "spring": "Spring", "summer": "Summer",
    "fall": "Fall", "autumn": "Fall", "winter": "Winter",
}

# MARC 21 coded chronology values used in 863 $j:
# months 01-12, seasons 21 (Spring) 22 (Summer) 23 (Autumn/Fall) 24 (Winter).
MARC_CHRON_CODES: dict[str, str] = {
    "jan": "01", "january": "01",
    "feb": "02", "february": "02",
    "mar": "03", "march": "03",
    "apr": "04", "april": "04",
    "may": "05",
    "jun": "06", "june": "06",
    "jul": "07", "july": "07",
    "aug": "08", "august": "08",
    "sep": "09", "sept": "09", "september": "09",
    "oct": "10", "october": "10",
    "nov": "11", "november": "11",
    "dec": "12", "december": "12",
    "spring": "21",
    "summer": "22",
    "fall": "23", "autumn": "23",
    "winter": "24",
}

SEASON_CODES = {"21", "22", "23", "24"}


def normalise_chron_unit(raw: str) -> Optional[str]:
    """Normalise a month or season string to MARC-standard text form."""
    if not raw:
        return None
    raw = raw.strip().rstrip(".")
    key = raw.lower()
    if key in MONTH_MAP:
        return MONTH_MAP[key]
    if key in SEASON_MAP:
        return SEASON_MAP[key]
    return raw  # return as-is (e.g. "Spr.", user-supplied)


def chron_unit_code(raw: str) -> Optional[str]:
    """Return the MARC coded chronology value for a month/season, or None."""
    if not raw:
        return None
    return MARC_CHRON_CODES.get(raw.strip().rstrip(".").lower())


def chron_unit_value(raw: str) -> Optional[str]:
    """
    Convert a month/season to its MARC coded value (01-12 months, 21-24
    seasons) for use in 863 $j; fall back to normalised text if the token
    isn't a recognised month or season. Combined units ("Jan/Feb") are
    encoded part-by-part ("01/02").
    """
    if not raw:
        return None
    raw = raw.strip()
    if "/" in raw:
        parts = [p for p in raw.split("/") if p.strip()]
        codes = [chron_unit_code(p) for p in parts]
        if parts and all(c is not None for c in codes):
            return "/".join(codes)
    code = chron_unit_code(raw)
    return code if code is not None else normalise_chron_unit(raw)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class EnumChron:
    """One boundary (start or end) of a holdings range."""
    vol: Optional[str] = None        # volume number string
    issue: Optional[str] = None      # issue / number string
    part: Optional[str] = None       # part number string
    year: Optional[str] = None       # four-digit year string
    month: Optional[str] = None      # month/season as MARC code (01-12, 21-24)
    day: Optional[str] = None        # day (uncommon for journals)

    def has_enum(self) -> bool:
        return any([self.vol, self.issue, self.part])

    def has_chron(self) -> bool:
        return any([self.year, self.month, self.day])

    def __str__(self) -> str:
        parts = []
        if self.vol:
            parts.append(f"v.{self.vol}")
        if self.issue:
            parts.append(f"no.{self.issue}")
        if self.part:
            parts.append(f"pt.{self.part}")
        chron_parts = []
        if self.year:
            chron_parts.append(self.year)
        if self.month:
            chron_parts.append(self.month)
        if chron_parts:
            parts.append(f"({'  :'.join(chron_parts)})")
        return "".join(parts)


@dataclass
class HoldingsRange:
    """A single holdings range (start–end, or start– if open)."""
    start: EnumChron = field(default_factory=EnumChron)
    end: Optional[EnumChron] = None   # None means open-ended
    open_ended: bool = False          # True ⇒ still being received
    raw: str = ""                     # original text for this range

    def caption_levels(self) -> dict:
        """Return which caption levels appear in this range."""
        levels: dict = {}
        for ec in [self.start, self.end]:
            if ec is None:
                continue
            if ec.vol is not None:
                levels["vol"] = True
            if ec.issue is not None:
                levels["issue"] = True
            if ec.part is not None:
                levels["part"] = True
            if ec.year is not None:
                levels["year"] = True
            if ec.month is not None:
                levels["month"] = True
        return levels


@dataclass
class ParseResult:
    """Result of parsing a single 866 $a value."""
    ranges: List[HoldingsRange] = field(default_factory=list)
    raw: str = ""
    warnings: List[str] = field(default_factory=list)
    success: bool = True

    def caption_union(self) -> dict:
        """Union of caption levels across all ranges."""
        union: dict = {}
        for r in self.ranges:
            union.update(r.caption_levels())
        return union


# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

# Standard captioned enumeration+chronology unit:
#   v.1:no.2(1990:Mar.)  Vol.1,No.2(Spring 1990)
_ENUM_CHRON_RE = re.compile(
    r"""
    (?:
      # --- Enumeration block (requires vol caption) ---
      (?P<vol_cap>v(?:ol(?:ume)?)?)\s*[.\s]*\s*(?P<vol_num>\d+[a-zA-Z]?)
      (?:
        [\s:,]\s*
        (?P<iss_cap>(?:no|n|nr|num(?:ber)?|iss(?:ue)?|pt|part))\s*[.\s]*\s*
        (?P<iss_num>\d+[a-zA-Z]?)
      )?
      (?:
        [\s:,]\s*
        (?P<pt_cap>pt|part)\s*[.\s]*\s*
        (?P<pt_num>\d+[a-zA-Z]?)
      )?
      \s*
    )?
    (?:
      # --- Chronology block ---
      \(
        (?P<chron_raw>[^)]+)
      \)
    )?
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Bare-number volume format — no "v." caption required:
#   "34 no 3 (Summer 1990)"  "34(1990)"  "34"
# Used as a fallback when _ENUM_CHRON_RE fails.
_BARE_NUM_RE = re.compile(
    r"""
    ^(?P<vol_num>\d+[a-zA-Z]?)
    (?:
      [\s,]\s*
      (?P<iss_cap>(?:no|n|nr|num(?:ber)?|iss(?:ue)?|pt|part))\s*[.\s]*\s*
      (?P<iss_num>\d+[a-zA-Z]?)
    )?
    \s*
    (?:
      \((?P<chron_raw>[^)]+)\)
    )?
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Year-only (bare four-digit year, no parens)
_YEAR_ONLY_RE = re.compile(r"^\s*(\d{4})\s*$")

# Volume-level caption at the start of a segment — used by _split_ranges
_VOL_START_RE = re.compile(
    r"^\s*(?:v(?:ol(?:ume)?)?)\s*[.\s]", re.IGNORECASE
)
_YEAR_START_RE = re.compile(r"^\s*\d{4}\s*(?:$|-)")


# ---------------------------------------------------------------------------
# Chronology helpers
# ---------------------------------------------------------------------------

def _parse_chron(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse a single-valued chronology string. Months/seasons are returned as
    MARC coded values (01-12 months, 21-24 seasons) for use in 863 $j.
      '1990'        → ('1990', None)
      '1990:Mar.'   → ('1990', '03')
      'Spring 1990' → ('1990', '21')
      'Mar. 1990'   → ('1990', '03')
      '1990 Spring' → ('1990', '21')
    Returns (year, month_or_season).
    """
    raw = raw.strip()

    # YYYY:Mon. or YYYY:Season
    m = re.match(r"(\d{4})\s*[:]\s*(.+)$", raw)
    if m:
        return m.group(1), chron_unit_value(m.group(2))

    # Mon./Season YYYY  (chron before year)
    m = re.match(r"([A-Za-z.]+(?:\s+[A-Za-z.]+)?)\s+(\d{4})$", raw)
    if m:
        return m.group(2), chron_unit_value(m.group(1))

    # YYYY Mon./Season  (year then chron)
    m = re.match(r"(\d{4})\s+([A-Za-z.]+(?:\s+[A-Za-z.]+)?)$", raw)
    if m:
        return m.group(1), chron_unit_value(m.group(2))

    # Bare four-digit year
    m = re.match(r"^(\d{4})$", raw)
    if m:
        return m.group(1), None

    # Give up — return the raw string so nothing is silently lost
    return raw, None


def _parse_chron_range(
    raw: str,
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Parse a chronology block that may itself span a range.
    Returns (start_year, start_month, end_year, end_month), with months and
    seasons as MARC coded values (01-12 months, 21-24 seasons).

    Handles:
      '1974-1976'              → ('1974', None, '1976', None)
      '1990:Jan.-1994:Dec.'    → ('1990', '01', '1994', '12')
      '1981-Fall 1983'         → ('1981', None, '1983', '23')
      'April 1992-April 1996'  → ('1992', '04', '1996', '04')
      'June 1996-1997'         → ('1996', '06', '1997', None)
      'Spring 1990-Winter 1994'→ ('1990', '21', '1994', '24')
      'Summer 1990'            → ('1990', '22', None, None)
      '1990:Jan.'              → ('1990', '01', None, None)
    """
    raw = raw.strip()

    # ── Range patterns ──────────────────────────────────────────────────────

    # YYYY-YYYY  (bare year range, e.g. "1974-1976")
    m = re.match(r"^(\d{4})\s*-\s*(\d{4})$", raw)
    if m:
        return m.group(1), None, m.group(2), None

    # YYYY:Mon-YYYY:Mon  or  YYYY:Mon-YYYY
    m = re.match(
        r"^(\d{4})\s*[:]\s*([A-Za-z][^-]*?)\s*-\s*(\d{4})(?:\s*[:]\s*(.+))?$",
        raw,
    )
    if m:
        return (
            m.group(1), chron_unit_value(m.group(2).strip()),
            m.group(3), chron_unit_value(m.group(4).strip()) if m.group(4) else None,
        )

    # YYYY-Season/Mon YYYY  (e.g. "1981-Fall 1983")
    m = re.match(
        r"^(\d{4})\s*-\s*([A-Za-z][A-Za-z.]*(?:\s+[A-Za-z.]+)?)\s+(\d{4})$",
        raw,
    )
    if m:
        return m.group(1), None, m.group(3), chron_unit_value(m.group(2))

    # Season/Mon YYYY-Season/Mon YYYY  (e.g. "April 1992-April 1996")
    m = re.match(
        r"^([A-Za-z][A-Za-z.]*(?:\s+[A-Za-z.]+)?)\s+(\d{4})\s*-\s*"
        r"([A-Za-z][A-Za-z.]*(?:\s+[A-Za-z.]+)?)\s+(\d{4})$",
        raw,
    )
    if m:
        return (
            m.group(2), chron_unit_value(m.group(1)),
            m.group(4), chron_unit_value(m.group(3)),
        )

    # Season/Mon YYYY-YYYY  (e.g. "June 1996-1997")
    m = re.match(
        r"^([A-Za-z][A-Za-z.]*(?:\s+[A-Za-z.]+)?)\s+(\d{4})\s*-\s*(\d{4})$",
        raw,
    )
    if m:
        return m.group(2), chron_unit_value(m.group(1)), m.group(3), None

    # ── Single-value fallback ────────────────────────────────────────────────
    year, month = _parse_chron(raw)
    return year, month, None, None


# ---------------------------------------------------------------------------
# Range splitting helpers
# ---------------------------------------------------------------------------

def _split_ranges(text: str) -> List[str]:
    """
    Split a holdings string into individual range strings on commas/semicolons
    that are NOT inside parentheses AND that are followed by a vol caption or
    bare year, or preceded by a closing parenthesis.
    """
    depth = 0
    candidates: List[int] = []
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch in (",", ";") and depth == 0:
            before = text[:i].rstrip()
            after = text[i + 1:].lstrip()
            if (
                before.endswith(")")
                or bool(_VOL_START_RE.match(after))
                or bool(_YEAR_START_RE.match(after))
            ):
                candidates.append(i)

    if not candidates:
        return [text.strip()]

    results: List[str] = []
    prev = 0
    for pos in candidates:
        seg = text[prev:pos].strip()
        if seg:
            results.append(seg)
        prev = pos + 1
    seg = text[prev:].strip()
    if seg:
        results.append(seg)
    return results


def _smart_split_range(text: str) -> List[str]:
    """
    Split "start-end" at the hyphen that separates the two major units.

    Preference order for choosing the split hyphen (all must be at paren depth 0):
      1. Hyphen preceded by ")"
      2. Hyphen between a digit and an alphabetic character (caption boundary)
      3. Last hyphen at depth 0 (fallback)
    """
    depth = 0
    candidates: List[int] = []
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "-" and depth == 0:
            candidates.append(i)

    if not candidates:
        return [text]

    best: Optional[int] = None
    for pos in candidates:
        before = text[pos - 1] if pos > 0 else ""
        after = text[pos + 1] if pos + 1 < len(text) else ""
        if before == ")" or (before.isdigit() and after.isalpha()):
            best = pos
            break

    if best is None:
        best = candidates[-1]

    left = text[:best].strip()
    right = text[best + 1:].strip()
    return [left, right] if right else [left]


# ---------------------------------------------------------------------------
# Unit parser
# ---------------------------------------------------------------------------

def _parse_unit(text: str) -> Optional[EnumChron]:
    """
    Parse a single enumeration+chronology boundary.
    Tries captioned format first, then bare-number format.
    Returns None if nothing meaningful is found.
    """
    text = text.strip()
    if not text:
        return None

    # Bare four-digit year (e.g. "1990")
    m = _YEAR_ONLY_RE.match(text)
    if m:
        return EnumChron(year=m.group(1))

    # Standard captioned format: v.1:no.2(1990:Mar.)
    m = _ENUM_CHRON_RE.match(text)
    if m and (m.group("vol_num") or m.group("chron_raw")):
        ec = EnumChron()
        if m.group("vol_num"):
            ec.vol = m.group("vol_num")
        if m.group("iss_num"):
            ec.issue = m.group("iss_num")
        if m.group("pt_num"):
            ec.part = m.group("pt_num")
        if m.group("chron_raw"):
            ec.year, ec.month = _parse_chron(m.group("chron_raw"))
        return ec if (ec.has_enum() or ec.has_chron()) else None

    # Bare-number format: "34 no 3 (Summer 1990)" — volume without caption
    # Only attempt this when the text contains an issue caption or chron paren;
    # a lone bare digit is too ambiguous and will be handled by the compressed-
    # range path in _parse_one_range instead.
    m = _BARE_NUM_RE.match(text)
    if m and (m.group("iss_num") or m.group("chron_raw")):
        ec = EnumChron()
        ec.vol = m.group("vol_num")
        if m.group("iss_num"):
            ec.issue = m.group("iss_num")
        if m.group("chron_raw"):
            ec.year, ec.month = _parse_chron(m.group("chron_raw"))
        return ec if (ec.has_enum() or ec.has_chron()) else None

    return None


# ---------------------------------------------------------------------------
# Range parser
# ---------------------------------------------------------------------------

# Matches a bare end-of-range number with an optional trailing chron paren:
#   "3"               → end vol/issue number only
#   "3 (1974-1976)"   → end number + chron range
_BARE_END_RE = re.compile(r"^(\d+[a-zA-Z]?)\s*(?:\(([^)]+)\))?\s*$")


def _parse_one_range(raw: str) -> HoldingsRange:
    """
    Parse a single range string such as:
      "v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)"
      "v.1-3(1974-1976)"                    ← compressed vol + year range
      "v.8 no.3-v.10 no.2(1981-Fall 1983)" ← trailing chron range
      "v.6(1995)-"                          ← open-ended
    """
    raw = raw.strip()
    hr = HoldingsRange(raw=raw)

    # Detect open-ended (trailing bare hyphen)
    open_ended = bool(re.search(r"-\s*$", raw))
    if open_ended:
        raw_trimmed = re.sub(r"-\s*$", "", raw).strip()
        hr.open_ended = True
    else:
        raw_trimmed = raw

    parts = _smart_split_range(raw_trimmed)

    # ── Single-unit (no hyphen found) ────────────────────────────────────────
    if len(parts) == 1:
        hr.start = _parse_unit(parts[0]) or EnumChron()
        return hr

    # ── Two-part split ───────────────────────────────────────────────────────
    start_text = parts[0].strip()
    end_text   = parts[1].strip()

    hr.start = _parse_unit(start_text) or EnumChron()

    # ── Case A: Compressed range ─────────────────────────────────────────────
    # The end side is a bare number (possibly + chron paren), not a full unit.
    # e.g. start="v. 1"  end="3 (1974-1976)"
    bare_m = _BARE_END_RE.match(end_text)
    parsed_end = _parse_unit(end_text)

    if bare_m and parsed_end is None:
        # The bare number is the end value at the deepest enum level.
        end_num   = bare_m.group(1)
        chron_str = bare_m.group(2)  # may be None

        end = EnumChron()
        if hr.start.vol and not hr.start.issue and not hr.start.part:
            end.vol = end_num
        elif hr.start.issue:
            end.issue = end_num
            end.vol   = hr.start.vol   # same volume
        elif hr.start.part:
            end.part = end_num
        else:
            end.vol = end_num

        if chron_str:
            sy, sm, ey, em = _parse_chron_range(chron_str)
            # Distribute chron range across start and end
            hr.start.year  = sy
            hr.start.month = sm
            end.year  = ey if ey is not None else sy
            end.month = em if em is not None else sm

        hr.end = end
        return hr

    # ── Case B: Normal two-unit split ────────────────────────────────────────
    hr.end = parsed_end

    if hr.end is None:
        return hr

    # ── Case C: Trailing chron range ─────────────────────────────────────────
    # Start has no chronology; end's chron block contains a range.
    # e.g. "v.8 no.3" / "v.10 no.2 (1981-Fall 1983)"
    # Also catches "v.27 no.4" / "v.31 no.4 (April 1992-April 1996)"
    if not hr.start.has_chron() and hr.end.year:
        sy, sm, ey, em = _parse_chron_range(hr.end.year)
        if ey is not None:
            # end.year was actually a range — distribute to start and end
            hr.start.year  = sy
            hr.start.month = sm
            hr.end.year    = ey
            hr.end.month   = em
        elif sm is not None and hr.end.month is None:
            # Single-value chron was mis-parsed (e.g. "June 1990" → year="June 1990")
            # Correct it in place
            hr.end.year  = sy
            hr.end.month = sm

    return hr


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_866(text: str) -> ParseResult:
    """
    Parse a MARC 866 $a textual holdings string.

    Returns a ParseResult with one or more HoldingsRange objects and
    any warnings generated during parsing.

    Examples
    --------
    >>> r = parse_866("v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)")
    >>> r.ranges[0].start.vol
    '1'
    >>> r.ranges[0].end.year
    '1994'
    >>> r = parse_866("v.1-3(1974-1976)")
    >>> r.ranges[0].end.vol
    '3'
    >>> r.ranges[0].end.year
    '1976'
    """
    result = ParseResult(raw=text)

    if not text or not text.strip():
        result.success = False
        result.warnings.append("Empty holdings string.")
        return result

    segments = _split_ranges(text)
    for seg in segments:
        hr = _parse_one_range(seg)
        if not hr.start.has_enum() and not hr.start.has_chron():
            result.warnings.append(
                f"Could not parse segment: '{seg}' — it will be skipped."
            )
            continue
        result.ranges.append(hr)

    if not result.ranges:
        result.success = False
        result.warnings.append(
            "No recognisable holdings ranges found. "
            "Please check the input format."
        )

    return result
    