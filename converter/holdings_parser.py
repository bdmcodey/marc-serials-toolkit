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
  v.6(1995)-                          ← open-ended / current
  Vol. 1, No. 1 (Spring 1990)-...
  1990-1994                           ← year-only holdings
  v.1-5(1990-1994)                    ← compressed range format
  v.1:no.1-v.2:no.4(1990-1991)       ← chron at end only
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
#
# Ported from the marc_853_encoding table in ai-regex/test_enum_update.py,
# which derives from extract.py by Phani Chaitanya Pendyala (MIT).
# See THIRD-PARTY-NOTICES.md.
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
    "spring": "21", "spr": "21",
    "summer": "22", "sum": "22",
    "fall": "23", "autumn": "23", "aut": "23",
    "winter": "24", "win": "24", "wint": "24",
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
class EnumLevel:
    """
    One level of enumeration: the caption a statement used, and its value.

    Both matter, and they are separate things.  MARC 853 carries enumeration
    captions in $a-$f in descending order of significance, and the *word* in
    each is just a label -- "no." in $a is a serial numbered by issue with no
    volume above it, which is ordinary.  Tying the level to the word ("issue
    means $b") is what made those statements unconvertible.
    """
    caption: Optional[str] = None    # as written, normalised: "v.", "no.", "pt."
    value: Optional[str] = None      # "1", "1-5", "1/2"


@dataclass
class EnumChron:
    """One boundary (start or end) of a holdings range."""
    # Enumeration levels, most significant first.  A serial numbered only by
    # issue has one level whose caption is "no."; a volume/issue/part serial
    # has three.  Position in this list is the level -- nothing else decides it.
    enum: List[EnumLevel] = field(default_factory=list)
    year: Optional[str] = None       # four-digit year string
    month: Optional[str] = None      # month or season (normalised)
    day: Optional[str] = None        # day (uncommon for journals)

    def level(self, index: int) -> Optional[EnumLevel]:
        """The enumeration level at `index`, or None when there is none."""
        return self.enum[index] if index < len(self.enum) else None

    def value_at(self, index: int) -> Optional[str]:
        lvl = self.level(index)
        return lvl.value if lvl else None

    def has_enum(self) -> bool:
        return any(lvl.value for lvl in self.enum)

    def has_chron(self) -> bool:
        return any([self.year, self.month, self.day])

    def __str__(self) -> str:
        parts = [f"{lvl.caption or ''}{lvl.value}"
                 for lvl in self.enum if lvl.value]
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
    open_ended: bool = False          # True  ⇒ still being received
    raw: str = ""                     # original text for this range

    def enum_depth(self) -> int:
        """How many enumeration levels either boundary of this range states."""
        return max((len(ec.enum) for ec in (self.start, self.end) if ec),
                   default=0)

    def enum_captions(self) -> List[Optional[str]]:
        """
        The caption for each enumeration level, most significant first.

        Taken from whichever boundary states one, since a range often writes
        its captions only at the start ("v. 1 no. 1-v. 5 no. 4" writes them
        twice, "v. 1-v. 5 no. 4" only once).
        """
        captions: List[Optional[str]] = [None] * self.enum_depth()
        for ec in (self.start, self.end):
            if ec is None:
                continue
            for i, lvl in enumerate(ec.enum):
                if captions[i] is None and lvl.caption:
                    captions[i] = lvl.caption
        return captions

    def caption_levels(self) -> dict:
        """Which levels appear in this range: enumeration depth plus chronology."""
        levels: dict = {}
        depth = self.enum_depth()
        if depth:
            levels["enum_depth"] = depth
            levels["enum_captions"] = self.enum_captions()
        for ec in [self.start, self.end]:
            if ec is None:
                continue
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
        """
        Union of levels across all ranges.

        Enumeration depth is the deepest any range reaches, and each level's
        caption comes from the first range that names it -- one 853 has to
        describe every 863 linked to it, so it declares as many levels as the
        fullest statement uses.
        """
        union: dict = {}
        captions: List[Optional[str]] = []
        for r in self.ranges:
            levels = r.caption_levels()
            for key in ("year", "month"):
                if levels.get(key):
                    union[key] = True
            for i, cap in enumerate(levels.get("enum_captions", [])):
                if i >= len(captions):
                    captions.append(cap)
                elif captions[i] is None:
                    captions[i] = cap
        if captions:
            union["enum_depth"] = len(captions)
            union["enum_captions"] = captions
        return union


# ---------------------------------------------------------------------------
# Tokeniser / regex helpers
# ---------------------------------------------------------------------------

# Matches a single enumeration+chronology unit such as:
#   v.1:no.2(1990:Mar.)  or  Vol.1,No.2(Spring 1990)  or  1990
#
# Group names used below:
#   vol_cap   – caption word for volume   (v, vol, volume)
#   vol_num   – volume number
#   iss_cap   – caption word for issue    (no, n, nr, num, number, issue, iss, pt, part)
#   iss_num   – issue number
#   chron_raw – everything inside ( )
#   year_only – bare year with no parens

# Caption words, and the normalised form each is written back as.  The word
# says what a level is *called*, never which level it is: "no." is ordinary in
# $a for a serial numbered by issue with no volume above it.
_CAPTION_WORDS = (
    (r"v(?:ol(?:ume)?)?", "v."),
    (r"nos?|n|nr|num(?:ber)?s?|iss(?:ue)?s?", "no."),
    (r"pts?|parts?", "pt."),
    (r"ser(?:ies)?", "ser."),
)
_CAPTION_ALT = "|".join(alt for alt, _ in _CAPTION_WORDS)

# One enumeration level: an optional caption, then its value.  The value may be
# a range ("1-5") or a combined designation ("7/8"), the two forms holdings use
# to compress a level.
_ENUM_LEVEL_RE = re.compile(
    rf"""
    (?:(?P<cap>{_CAPTION_ALT})\s*[.\s]*\s*)?
    (?P<num>\d+[a-zA-Z]?(?:\s*[-/]\s*\d+[a-zA-Z]?)?)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# What separates one enumeration level from the next: ":", "," or whitespace.
_LEVEL_SEP_RE = re.compile(r"^[\s:,]\s*")

# The chronology block, in parentheses after the enumeration.
_CHRON_BLOCK_RE = re.compile(r"\(\s*(?P<chron_raw>[^)]+)\)")


def normalise_caption(raw: Optional[str]) -> Optional[str]:
    """
    The standard written form of a caption word: "Vol."/"volume" -> "v.".

    The word is preserved, the style is not.  Without this, "v. 1 no. 1" and
    "Vol. 1, No. 1" would build 853s that differ only in punctuation and stop
    sharing one field across a record.
    """
    if not raw:
        return None
    key = raw.strip().rstrip(".").lower()
    for alt, canonical in _CAPTION_WORDS:
        if re.fullmatch(alt, key, re.IGNORECASE):
            return canonical
    return raw.strip()


def _parse_enum_levels(text: str) -> Tuple[List[EnumLevel], int]:
    """
    Read consecutive enumeration levels off the front of `text`.

    Returns the levels and how far into `text` they reached.  Levels are taken
    in the order they are written -- position is the level, and the caption
    word is carried along rather than deciding anything.
    """
    levels: List[EnumLevel] = []
    pos = 0
    while pos < len(text):
        chunk = text[pos:]
        if levels:
            sep = _LEVEL_SEP_RE.match(chunk)
            if not sep:
                break
            chunk = chunk[sep.end():]
            offset = pos + sep.end()
        else:
            offset = pos

        m = _ENUM_LEVEL_RE.match(chunk)
        if not m or not m.group("num"):
            break
        # A second or later level must name itself.  Without that rule the
        # "18" of "Apr 18, 1996" or a stray number after a caption would be
        # swallowed as another level.
        if levels and not m.group("cap"):
            break
        levels.append(EnumLevel(caption=normalise_caption(m.group("cap")),
                                value=m.group("num").strip()))
        pos = offset + m.end()

    return levels, pos


# Simpler pattern for year-only holdings (e.g. "1990" or "1990-1994")
_YEAR_ONLY_RE = re.compile(r"^\s*(\d{4})\s*$")

# Matches the start of a new range: a volume-level caption at the beginning
# e.g. "v.", "vol.", "volume" – but NOT "no.", "n.", "pt." etc.
_VOL_START_RE = re.compile(
    r"^\s*(?:v(?:ol(?:ume)?)?)\s*[.\s]", re.IGNORECASE
)
_YEAR_START_RE = re.compile(r"^\s*\d{4}\s*(?:$|-)")

def _is_designation_prefix(before: str, after: str) -> bool:
    """
    True when `before` heads the statement `after` rather than being a range.

    "Series 1, v. 6 no. 1 (Summer/Fall 1992)" is one statement: the series is a
    designation the volume sits under, and splitting it off leaves two ranges
    numbered by hierarchies that no single 853 can describe.  "v. 1, v. 5
    (1994)" is two ranges, and reads the same way to a cataloguer, so the test
    is whether the two sides number by the same caption: a repeated caption is
    a second range, a caption that appears only on the left is a heading.

    A designation states no chronology -- once it does, it is a range of its
    own whatever it is called.
    """
    if "(" in before or ")" in before:
        return False
    left, consumed = _parse_enum_levels(before)
    if not left or consumed < len(before.strip()):
        return False
    if any(lvl.caption is None for lvl in left):
        return False
    right, _ = _parse_enum_levels(after)
    right_captions = {lvl.caption for lvl in right if lvl.caption}
    if not right_captions:
        return False
    return not any(lvl.caption in right_captions for lvl in left)


def _split_ranges(text: str) -> List[str]:
    """
    Split a holdings string into individual range strings.

    Splits on comma or semicolon that is:
      - NOT inside parentheses, AND
      - Followed by a volume-level caption (v., Vol., etc.) OR a bare year,
        OR preceded by a closing parenthesis.

    This avoids splitting "Vol. 1, No. 1 (Spring 1990)" on the comma
    between the volume and issue captions, and -- see
    _is_designation_prefix -- on the comma after a series designation.
    """
    # Collect candidate split positions
    depth = 0
    candidates: List[int] = []
    segment_start = 0
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
            if not (preceded_by_close or followed_by_vol or followed_by_year):
                continue
            if _is_designation_prefix(text[segment_start:i], after):
                continue
            candidates.append(i)
            segment_start = i + 1

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


def _parse_chron_single(raw: str,
                        warnings: Optional[List[str]] = None,
                        ) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse ONE chronology boundary (no range hyphen), e.g.:
      '1990'  '1990:Mar.'  'Spring 1990'  '1990 Spring'  'Mar. 1990'  'Jan'
      'Apr 18, 1996'  -> ('1996', '04'), the day noted and not encoded
    Returns (year, month_code_or_text).
    """
    raw = raw.strip()
    if not raw:
        return None, None

    # Bare year
    m = re.match(r"^(\d{4})$", raw)
    if m:
        return m.group(1), None

    # Mon D, YYYY -- a day-level date.  Every other alternative here wants the
    # year adjacent to the month, so "Apr 18, 1996" matched none of them and
    # the whole boundary was returned as (None, None): the month and the year
    # went with the day.  863 $k holds a day and nothing in this tool models
    # one yet, so the day is dropped -- as the block grammar already drops it
    # -- but the month and year are kept, and the day is named.
    m = re.match(r"^([A-Za-z.]+)\s+(\d{1,2})\s*,?\s+(\d{4})$", raw)
    if m and chron_unit_code(m.group(1)) is not None:
        if warnings is not None:
            note = (f"Day-level date '{raw}' recorded to the month; "
                    f"the day ({m.group(2)}) is not encoded.")
            if note not in warnings:
                warnings.append(note)
        return m.group(3), _chron_unit_value(m.group(1))

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


def _parse_chron(raw: str,
                 warnings: Optional[List[str]] = None,
                 ) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse a chronology string, including ranges within a single group:
      '1990'          -> ('1990', None)
      '1990:Mar.'     -> ('1990', '03')
      'Jan-Jun 1984'  -> ('1984', '01-06')     # year shared across range
      'Jan 1990-Dec 1994' -> ('1990-1994', '01-12')
      'Jan 1956-Jan 1957' -> ('1956-1957', '01-01')   # both ends named, kept
      '1981-Sep 1996' -> ('1981-1996', None)   # one end only: cannot be placed
      '1990-1994'     -> ('1990-1994', None)
    Months and seasons are returned as MARC chronology codes
    (01-12, seasons 21-24) for use in 863 $j.
    Returns (year, month).
    """
    raw = raw.strip()

    if "-" in raw:
        left, right = (p.strip() for p in raw.split("-", 1))
        l_year, l_month = _parse_chron_single(left, warnings)
        r_year, r_month = _parse_chron_single(right, warnings)

        # Year: share the right-hand year if the left boundary omits it.
        # Equal years collapse: the year is the most significant chronology
        # level, so there is nothing above it whose endpoints a repeat would
        # be pairing with.
        if l_year and r_year:
            year = l_year if l_year == r_year else f"{l_year}-{r_year}"
        else:
            year = l_year or r_year

        # Month/season.  Two ends naming the same month keep both --
        # "Jan 1956 - Jan 1957" is '01-01', not '01'.  Collapsing it lost the
        # pairing with the years either side, leaving "$i 1956-1957 $j 01",
        # which reads as one January spanning two years.
        #
        # One end naming a month and the other not -- "1981 - Sep 1996",
        # "Aug 1984-1985" -- cannot be recorded at all.  A reader pairs the
        # subfields positionally, so "$i 1981-1996 $j 09" says the run *begins*
        # in September 1981, which the statement never claimed.  There is no
        # notation for a chronology that belongs to one end only, so the month
        # is dropped and named.  This is the only place that can tell the two
        # cases apart: by the time the converter sees a lone '09' it cannot know
        # whether the other end said the same thing or said nothing.
        if l_month and r_month:
            month = f"{l_month}-{r_month}"
        elif l_month or r_month:
            month = None
            if warnings is not None:
                lone = l_month or r_month
                note = (
                    f"Only one end of '{raw}' gives a month or season ({lone}); "
                    "with nothing at the other end it cannot be recorded as a "
                    "range, so it was left out."
                )
                if note not in warnings:
                    warnings.append(note)
        else:
            month = None

        if year or month:
            return year, month
        return raw, None  # unparseable: preserve raw so nothing is lost

    year, month = _parse_chron_single(raw, warnings)
    if year or month:
        return year, month

    # Give up - return raw as year string so the data is not dropped
    return raw, None


def _parse_unit(text: str,
                warnings: Optional[List[str]] = None,
                ) -> Optional[EnumChron]:
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
        return EnumChron(year=m.group(1))

    levels, pos = _parse_enum_levels(text)

    rest = text[pos:].lstrip()
    consumed = len(text) - len(rest)
    chron = _CHRON_BLOCK_RE.match(rest)
    if chron:
        consumed += chron.end()

    if not levels and not chron:
        return None

    # The match has to account for the whole unit, whether or not a caption is
    # present.  In "v. 19 nos. 1, 3, 5, 7-12 (Jan, Mar, May, Jul-Dec 1915)" it
    # reaches only as far as "v. 19 nos. 1", and in "v. 58 Suppl. (Sep 2003)"
    # only as far as "v. 58"; converting either alone is worse than converting
    # nothing, because the 866 is removed once anything is written from it and
    # the rest of the statement goes with it.
    if consumed != len(text):
        if warnings is not None:
            note = (
                f"Read '{text[:consumed].strip()}' but could not account for "
                f"'{text[consumed:].strip()}' — nothing was converted from this "
                "statement rather than convert part of it."
            )
            if note not in warnings:
                warnings.append(note)
        return None

    # A number with no caption of its own is only an enumeration level when
    # something else in the statement says so: a captioned level must follow
    # it.  "39 no 1" is v.39 no.1, because a number sitting a level above an
    # issue is a volume.  Without that anchor there is nothing to read the
    # level from, and "2016?" would become a volume rather than an uncertain
    # year.
    if levels and levels[0].caption is None and len(levels) == 1:
        return None

    ec = EnumChron(enum=levels)

    if chron:
        ec.year, ec.month = _parse_chron(chron.group("chron_raw"), warnings)

    return ec if (ec.has_enum() or ec.has_chron()) else None


def _parse_one_range(raw: str,
                     warnings: Optional[List[str]] = None,
                     ) -> HoldingsRange:
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
        start = _parse_unit(parts[0], warnings)
        hr.start = start or EnumChron()
    elif len(parts) >= 2:
        start = _parse_unit(parts[0], warnings)
        end = _parse_unit(parts[1], warnings)
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
    #
    # The neighbours are the nearest *non-space* characters, not the adjacent
    # ones. "v. 1 (2001) - v. 5 (2005)" is written with spaces around its
    # separator at least as often as without, and reading the spaces themselves
    # matched no rule: the statement parsed as a single unit, the end of the
    # range was dropped, an 863 was produced for the start alone, and the 866
    # was then removed as converted -- losing the holdings with no warning.
    best = None
    for pos in candidate_positions:
        before = text[:pos].rstrip()[-1:]
        after = text[pos + 1:].lstrip()[:1]
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
            item = item.strip()
            # A nested group -- the "(1)" in "N1984: (2 (1))" -- is not part of
            # the issue-and-chronology shape this grammar reads, and its meaning
            # is local to whoever wrote it. Named rather than dropped in
            # silence; guessing at it would be worse.
            nested = re.search(r"\(([^()]*)\)", item)
            if nested and nested.group(1).strip():
                note = (f"Nested group '({nested.group(1).strip()})' inside "
                        f"'{item}' is not encoded.")
                if note not in result.warnings:
                    result.warnings.append(note)

            im = _BLOCK_ITEM_RE.match(item)
            if not im:
                continue
            issue = (im.group("iss") or "").strip() or None
            c_start, c_end = _parse_bracket_chron(im.group("chron") or "")

            if not any([vol, issue, year, c_start]):
                continue

            # Positional, and it always was: a number *before* the parens is
            # the higher level and numbers *inside* are the lower one.  The
            # block grammar names neither, so the captions are the defaults.
            enum: List[EnumLevel] = []
            if vol:
                enum.append(EnumLevel(caption="v.", value=vol))
            if issue:
                enum.append(EnumLevel(caption="no.", value=issue))
            start = EnumChron(enum=enum, year=year, month=c_start)
            end = EnumChron(month=c_end) if c_end and c_end != c_start else None
            result.ranges.append(
                HoldingsRange(start=start, end=end, raw=item or body.strip())
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
    >>> r.ranges[0].start.enum[0].value
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

    # Notes from the unit parser are kept apart from the segment-level ones.
    # They say *why* a unit was refused, which is worth carrying onto the
    # degenerate path -- a truncated statement otherwise reports only "no
    # recognisable holdings ranges", which does not say that most of one was
    # read and deliberately not converted. The generic per-segment line is not
    # worth carrying: on that path it only repeats what the degenerate result
    # already says.
    segments = _split_ranges(text)
    notes: List[str] = []
    for seg in segments:
        seg_notes: List[str] = []
        hr = _parse_one_range(seg, seg_notes)
        notes.extend(w for w in seg_notes if w not in notes)
        if not hr.start.has_enum() and not hr.start.has_chron():
            result.warnings.append(
                f"Could not parse segment: '{seg}' — it will be skipped."
            )
            continue
        result.ranges.append(hr)

    result.warnings.extend(w for w in notes if w not in result.warnings)

    if not result.ranges:
        degenerate = _parse_degenerate(text)
        degenerate.warnings.extend(w for w in notes
                                   if w not in degenerate.warnings)
        return degenerate

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
