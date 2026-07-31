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
  v.6(1995)-                          â† open-ended / current
  Vol. 1, No. 1 (Spring 1990)-...
  1990-1994                           â† year-only holdings
  v.1-5(1990-1994)                    â† compressed range format
  v.1:no.1-v.2:no.4(1990-1991)       â† chron at end only
  Multiple ranges: "v.1(1990)-v.3(1992), v.5(1994)-"

Also supports a second, chronology-first "block" grammar found in older and
locally-maintained records, dispatched separately by _looks_like_block():
  1993: (1 [Feb])
  2019: (1-6 [Feb-Nov])2020: (7-12 [Jan-Dec])
  1949: 1 (1-6 [Apr-Sep])
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

# MARC 21 chronology codes used in 863 $j: months 01-12, seasons 21-24.
# (Ported from the marc_853_encoding table in test_enum_update.py.)
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


def chron_unit_code(raw: str) -> Optional[str]:
    """Return the MARC chronology code for a month/season name, or None."""
    return MARC_CHRON_CODES.get(raw.strip().rstrip(".").lower())

def normalise_chron_unit(raw: str) -> str:
    """Normalise a month or season string to MARC-standard form."""
    raw = raw.strip().rstrip(".")
    key = raw.lower()
    if key in MONTH_MAP:
        return MONTH_MAP[key]
    if key in SEASON_MAP:
        return SEASON_MAP[key]
    return raw  # return as-is (e.g. "Spr.", user-supplied)


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
    month: Optional[str] = None      # month or season (normalised)
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
    """A single holdings range (startâ€“end, or startâ€“ if open)."""
    start: EnumChron = field(default_factory=EnumChron)
    end: Optional[EnumChron] = None   # None means open-ended
    open_ended: bool = False          # True  â‡’ still being received
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
    needs_review: bool = False   # values were found but could not be placed

    def caption_union(self) -> dict:
        """Union of caption levels across all ranges."""
        union: dict = {}
        for r in self.ranges:
            union.update(r.caption_levels())
        return union


# ---------------------------------------------------------------------------
# Tokeniser / regex helpers
# ---------------------------------------------------------------------------

# Matches a single enumeration+chronology unit such as:
#   v.1:no.2(1990:Mar.)  or  Vol.1,No.2(Spring 1990)  or  1990
#
# Group names used below:
#   vol_cap   â€“ caption word for volume   (v, vol, volume)
#   vol_num   â€“ volume number
#   iss_cap   â€“ caption word for issue    (no, n, nr, num, number, issue, iss, pt, part)
#   iss_num   â€“ issue number
#   chron_raw â€“ everything inside ( )
#   year_only â€“ bare year with no parens

_ENUM_CHRON_RE = re.compile(
    r"""
    (?:
      # --- Enumeration block ---
      (?P<vol_cap>v(?:ol(?:ume)?)?)\s*[.\s]*\s*
      (?P<vol_num>\d+[a-zA-Z]?(?:\s*-\s*\d+[a-zA-Z]?)?)
      (?:
        [\s:,]\s*
        (?P<iss_cap>(?:nos?|n|nr|num(?:ber)?s?|iss(?:ue)?s?|pt|part))\s*[.\s]*\s*
        (?P<iss_num>\d+[a-zA-Z]?(?:\s*[-/]\s*\d+[a-zA-Z]?)?)
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

# Simpler pattern for year-only holdings (e.g. "1990" or "1990-1994")
_YEAR_ONLY_RE = re.compile(r"^\s*(\d{4})\s*$")

# Matches the start of a new range: a volume-level caption at the beginning
# e.g. "v.", "vol.", "volume" â€“ but NOT "no.", "n.", "pt." etc.
_VOL_START_RE = re.compile(
    r"^\s*(?:v(?:ol(?:ume)?)?)\s*[.\s]", re.IGNORECASE
)
_YEAR_START_RE = re.compile(r"^\s*\d{4}\s*(?:$|-)")

def _split_ranges(text: str) -> List[str]:
    """
    Split a holdings string into individual range strings.

    Splits on comma or semicolon that is:
      - NOT inside parentheses, AND
      - Followed by a volume-level caption (v., Vol., etc.) OR a bare year,
        OR preceded by a closing parenthesis.

    This avoids splitting "Vol. 1, No. 1 (Spring 1990)" on the comma
    between the volume and issue captions.
    """
    # Collect candidate split positions
    depth = 0
    candidates: List[int] = []
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch in (",", ";") and depth == 0:
            # Look back: is the preceding non-space character ")" or a digit?
            before = text[:i].rstrip()
            # Look ahead: what follows the separator?
            after = text[i + 1:].lstrip()
            preceded_by_close = before.endswith(")")
            followed_by_vol = bool(_VOL_START_RE.match(after))
            followed_by_year = bool(_YEAR_START_RE.match(after))
            if preceded_by_close or followed_by_vol or followed_by_year:
                candidates.append(i)

    if not candidates:
        return [text.strip()]

    results = []
    prev = 0
    for pos in candidates:
        segment = text[prev:pos].strip()
        if segment:
            results.append(segment)
        prev = pos + 1
    segment = text[prev:].strip()
    if segment:
        results.append(segment)
    return results


def _chron_unit_value(raw: str) -> str:
    """
    MARC code for a month/season if recognised, else normalised text.
    Combined issues ('Jan/Feb') are encoded part-by-part ('01/02').
    """
    if "/" in raw:
        parts = [p for p in raw.split("/") if p.strip()]
        codes = [chron_unit_code(p) for p in parts]
        if all(c is not None for c in codes):
            return "/".join(codes)
    code = chron_unit_code(raw)
    return code if code is not None else normalise_chron_unit(raw)


def _parse_chron_single(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse ONE chronology boundary (no range hyphen), e.g.:
      '1990'  '1990:Mar.'  'Spring 1990'  '1990 Spring'  'Mar. 1990'  'Jan'
    Returns (year, month_code_or_text).
    """
    raw = raw.strip()
    if not raw:
        return None, None

    # Bare year
    m = re.match(r"^(\d{4})$", raw)
    if m:
        return m.group(1), None

    # YYYY:Mon. or YYYY Season (year first)
    m = re.match(r"(\d{4})\s*[:\s]\s*([A-Za-z./]+(?:\s+[A-Za-z./]+)?)$", raw)
    if m:
        return m.group(1), _chron_unit_value(m.group(2))

    # Mon. YYYY or Season YYYY (chron before year)
    m = re.match(r"([A-Za-z./]+(?:\s+[A-Za-z./]+)?)\s*[:\s]\s*(\d{4})$", raw)
    if m:
        return m.group(2), _chron_unit_value(m.group(1))

    # Bare month/season name (year supplied by the other boundary,
    # e.g. the 'Jan' in 'Jan-Jun 1984')
    m = re.match(r"^([A-Za-z./]+)$", raw)
    if m:
        return None, _chron_unit_value(m.group(1))

    return None, None


def _parse_chron(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse a chronology string, including ranges within a single group:
      '1990'          -> ('1990', None)
      '1990:Mar.'     -> ('1990', '03')
      'Jan-Jun 1984'  -> ('1984', '01-06')     # year shared across range
      'Jan 1990-Dec 1994' -> ('1990-1994', '01-12')
      '1990-1994'     -> ('1990-1994', None)
    Months and seasons are returned as MARC chronology codes
    (01-12, seasons 21-24) for use in 863 $j.
    Returns (year, month).
    """
    raw = raw.strip()

    if "-" in raw:
        left, right = (p.strip() for p in raw.split("-", 1))
        l_year, l_month = _parse_chron_single(left)
        r_year, r_month = _parse_chron_single(right)

        # Year: share the right-hand year if the left boundary omits it
        if l_year and r_year:
            year = l_year if l_year == r_year else f"{l_year}-{r_year}"
        else:
            year = l_year or r_year

        # Month/season range
        if l_month and r_month:
            month = l_month if l_month == r_month else f"{l_month}-{r_month}"
        else:
            month = l_month or r_month

        if year or month:
            return year, month
        return raw, None  # unparseable: preserve raw so nothing is lost

    year, month = _parse_chron_single(raw)
    if year or month:
        return year, month

    # Give up - return raw as year string so the data is not dropped
    return raw, None


def _parse_unit(text: str) -> Optional[EnumChron]:
    """
    Parse a single enumeration+chronology unit (one boundary of a range).
    Returns None if nothing meaningful is found.
    """
    text = text.strip()
    if not text:
        return None

    # Year-only shorthand
    m = _YEAR_ONLY_RE.match(text)
    if m:
        ec = EnumChron(year=m.group(1))
        return ec

    m = _ENUM_CHRON_RE.match(text)
    if not m or (not m.group("vol_num") and not m.group("chron_raw")):
        return None

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


def _parse_one_range(raw: str) -> HoldingsRange:
    """
    Parse a single range string like:
      "v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)"
      "v.6(1995)-"    (open-ended)
    """
    raw = raw.strip()
    hr = HoldingsRange(raw=raw)

    # Check for open-ended (ends with bare "-")
    open_ended = bool(re.search(r"-\s*$", raw))
    if open_ended:
        raw_trimmed = re.sub(r"-\s*$", "", raw).strip()
        hr.open_ended = True
    else:
        raw_trimmed = raw

    # Split on the range separator "-" that lies between two units.
    # Strategy: split on " - " or hyphen NOT inside parentheses and not
    # part of a number like "no.1-4".
    #
    # We find the "-" that separates two major enum-chron units by
    # scanning for a hyphen that is:
    #   1. Not inside parentheses
    #   2. Preceded by a digit or ")"
    #   3. Followed by a letter (start of a caption) or digit or whitespace
    parts = _smart_split_range(raw_trimmed)

    if len(parts) == 1:
        start = _parse_unit(parts[0])
        hr.start = start or EnumChron()
    elif len(parts) >= 2:
        start = _parse_unit(parts[0])
        end = _parse_unit(parts[1])
        hr.start = start or EnumChron()
        hr.end = end

    return hr


def _smart_split_range(text: str) -> List[str]:
    """
    Split "start-end" at the hyphen separating the two major units.
    Handles hyphens inside parentheses (chronology ranges like 1990-1994
    inside parens) and compressed formats like "v.1-5(1990-1994)".
    """
    depth = 0
    candidate_positions = []
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "-" and depth == 0:
            candidate_positions.append(i)

    if not candidate_positions:
        return [text]

    # Prefer the split that produces two non-trivial units.
    # Heuristic: prefer positions where the character before is ")" or digit
    # and after is alpha (start of caption) or "(" or digit.
    best = None
    for pos in candidate_positions:
        before = text[pos - 1] if pos > 0 else ""
        after = text[pos + 1] if pos + 1 < len(text) else ""
        if before in (")", ) or (before.isdigit() and after.isalpha()):
            best = pos
            break
        # Fallback: any hyphen between digit and alpha
        if before.isdigit() and after.isalpha():
            best = pos
            break

    if best is None:
        # Year-only range shorthand ("1990-1994") still splits on its hyphen.
        if re.fullmatch(r"\s*\d{4}\s*-\s*\d{4}\s*", text):
            best = candidate_positions[0]
        else:
            # Every remaining candidate is a digit-digit hyphen, i.e. a
            # range WITHIN one caption level ("nos. 1-6", "v.1-5") rather
            # than a start/end separator.  Parse the string as one unit
            # and let _ENUM_CHRON_RE capture the range tokens.
            return [text]

    left = text[:best].strip()
    right = text[best + 1:].strip()
    return [left, right] if right else [left]


# ---------------------------------------------------------------------------
# Block ("chronology-first") format
# ---------------------------------------------------------------------------
#
# A second holdings grammar, common in older and locally-maintained records:
#
#     1993: (1 [Feb])
#     2019: (1-6 [Feb-Nov])2020: (7-12 [Jan-Dec])2021: (13-15 [Feb-Jun])
#     1949: 1 (1-6 [Apr-Sep])
#     N2002: ([Mar], [Jul], [Aug])2005: ([Aug])
#
# Year comes first, then an optional volume, then a parenthesised body of
# comma-separated "issue [chronology]" items.  Blocks repeat with no separator
# between them, and each item becomes its own 863.
#
# This is a different grammar from _ENUM_CHRON_RE above, not a looser version
# of it, so it gets its own parser and is dispatched by _looks_like_block().

_BLOCK_RE = re.compile(
    r"""
    (?P<marker>[NM])?\s*                 # unexplained local marker
    (?P<year>\d{4}|\?)\s*:?\s*           # year, or '?' for unknown
    (?:v(?:ol(?:ume)?)?\.?\s*)?          # optional volume caption
    (?P<vol>\d+)?\s*                     # volume number, outside the parens
    \(\s*(?P<body>[^()]*(?:\([^()]*\)[^()]*)*)\)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# One item inside a block body: "1-4 [Jan 5-Jan 26]", "[Aug]", "12"
_BLOCK_ITEM_RE = re.compile(
    r"(?P<iss>\d+[a-z]?(?:\s*-\s*\d+[a-z]?)?)?\s*"
    r"(?:\[(?P<chron>[^\]]*)\])?",
    re.IGNORECASE,
)

# Split a block body on commas that are not inside a [...] chronology group
_BLOCK_BODY_SPLIT_RE = re.compile(r",(?![^\[]*\])")

# A statement is in block format when it opens with "YEAR:" or "?:"
_BLOCK_SNIFF_RE = re.compile(r"^\s*[NM]?\s*(?:\d{4}|\?)\s*:", re.IGNORECASE)

# Curly-brace cataloguer notes: "{Memorial Issue}", "{2nd printing}"
_BRACE_NOTE_RE = re.compile(r"\{([^}]*)\}?")


def _looks_like_block(text: str) -> bool:
    """True when `text` uses the chronology-first block grammar."""
    return bool(_BLOCK_SNIFF_RE.match(text))


def _bracket_chron_unit(raw: str) -> Optional[str]:
    """
    MARC chronology code for one side of a bracketed group.

    'Jun 1'    -> '06'      (trailing day dropped)
    'Jul/Aug'  -> '07/08'   (combined issue, via _chron_unit_value)
    'summer'   -> '22'
    'Sum'      -> 'Sum'     (unrecognised: preserved, not dropped)
    """
    raw = raw.strip()
    if not raw:
        return None
    m = re.match(r"([A-Za-z]+(?:\s*/\s*[A-Za-z]+)*)", raw)
    if not m:
        return None
    return _chron_unit_value(re.sub(r"\s*", "", m.group(1)))


def _parse_bracket_chron(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse a bracketed chronology group into (start, end) codes.

    '[Feb]'            -> ('02', None)
    '[Feb-Nov]'        -> ('02', '11')
    '[Jan 5-Jan 26]'   -> ('01', '01')
    '[Jul/Aug]'        -> ('07/08', None)
    """
    raw = raw.strip()
    if not raw:
        return None, None
    if "-" in raw:
        left, right = (p.strip() for p in raw.split("-", 1))
        return _bracket_chron_unit(left), _bracket_chron_unit(right)
    return _bracket_chron_unit(raw), None


def _parse_block_format(text: str) -> ParseResult:
    """
    Parse the chronology-first block grammar into HoldingsRange objects.

    Role assignment is positional and therefore determinate: a number *before*
    the parentheses is the volume, numbers *inside* are issues.  Statements
    whose numbers sit in neither position are left unconverted and flagged for
    review rather than guessed at.
    """
    result = ParseResult(raw=text)

    for note in _BRACE_NOTE_RE.findall(text):
        if note.strip():
            result.warnings.append(f"Cataloguer note preserved, not encoded: '{note.strip()}'.")

    markers = sorted({m.group("marker").upper()
                      for m in _BLOCK_RE.finditer(text) if m.group("marker")})
    if markers:
        result.warnings.append(
            f"Unexplained marker(s) {', '.join(markers)} found before the year — "
            "parsed around them; meaning not encoded."
        )

    for blk in _BLOCK_RE.finditer(text):
        year = blk.group("year")
        year = None if year == "?" else year
        vol = blk.group("vol")
        body = blk.group("body") or ""

        items = [i for i in _BLOCK_BODY_SPLIT_RE.split(body) if i.strip()]
        if not items:
            items = [""]          # "(...)" with nothing usable inside

        for item in items:
            im = _BLOCK_ITEM_RE.match(item.strip())
            if not im:
                continue
            issue = (im.group("iss") or "").strip() or None
            c_start, c_end = _parse_bracket_chron(im.group("chron") or "")

            if not any([vol, issue, year, c_start]):
                continue

            start = EnumChron(vol=vol, issue=issue, year=year, month=c_start)
            end = EnumChron(month=c_end) if c_end and c_end != c_start else None
            result.ranges.append(
                HoldingsRange(start=start, end=end, raw=item.strip() or body.strip())
            )

    if result.ranges:
        return result

    # ── Degenerate forms: "?: 2", "?: 16" — a value with no positional
    # evidence for whether it is a volume or an issue.  Extract it so it is
    # visible, but do not convert it.
    m = re.match(r"^\s*[NM]?\s*(?:(?P<year>\d{4})|\?)\s*:\s*(?P<num>\d+)\s*$", text)
    if m:
        result.needs_review = True
        result.success = False
        result.warnings.append(
            f"Found the number '{m.group('num')}' but nothing indicates whether it is "
            "a volume or an issue — left unconverted for review."
        )
        return result

    result.success = False
    result.warnings.append(
        "Looks like a year-first holdings statement but no usable block was found."
    )
    return result


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
    """
    result = ParseResult(raw=text)

    if not text or not text.strip():
        result.success = False
        result.warnings.append("Empty holdings string.")
        return result

    # Chronology-first records use a different grammar entirely; dispatch
    # before the enumeration-first path rather than trying to widen it.
    if _looks_like_block(text):
        return _parse_block_format(text)

    segments = _split_ranges(text)
    for seg in segments:
        hr = _parse_one_range(seg)
        if not hr.start.has_enum() and not hr.start.has_chron():
            result.warnings.append(
                f"Could not parse segment: '{seg}' â€” it will be skipped."
            )
            continue
        result.ranges.append(hr)

    if not result.ranges:
        return _parse_degenerate(text)

    return result


def _parse_degenerate(text: str) -> ParseResult:
    """
    Last resort for single-value statements that neither grammar accepts:
    "2016?", "? 106", "?: 16".

    A year alone is usable holdings data.  A bare number is not — nothing says
    which level it belongs to — so it is surfaced for review, never guessed.
    """
    result = ParseResult(raw=text)

    m = re.match(r"^\s*(?P<year>\d{4})\s*\?\s*$", text)
    if m:
        result.ranges.append(HoldingsRange(
            start=EnumChron(year=m.group("year")), raw=text.strip()
        ))
        result.warnings.append(
            f"Year '{m.group('year')}' recorded as uncertain ('?') in the source; "
            "the qualifier is not encoded."
        )
        return result

    m = re.match(r"^\s*\??\s*(?P<num>\d+)\s*$", text)
    if m:
        result.needs_review = True
        result.success = False
        result.warnings.append(
            f"Found the number '{m.group('num')}' but nothing indicates whether it is "
            "a volume, an issue or a year — left unconverted for review."
        )
        return result

    result.success = False
    result.warnings.append(
        "No recognisable holdings ranges found. "
        "Please check the input format."
    )
    return result
