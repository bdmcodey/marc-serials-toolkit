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


def _sole_offset(short: List[Optional[str]],
                 long: List[Optional[str]]) -> Optional[int]:
    """
    The one offset at which `short` sits inside `long`, or None if not exactly
    one does.  A missing caption on either side matches anything, since it
    states nothing to contradict.
    """
    fits = [k for k in range(len(long) - len(short) + 1)
            if all(a is None or b is None or a == b
                   for a, b in zip(short, long[k:]))]
    return fits[0] if len(fits) == 1 else None


@dataclass
class HoldingsRange:
    """A single holdings range (start–end, or start– if open)."""
    start: EnumChron = field(default_factory=EnumChron)
    end: Optional[EnumChron] = None   # None means open-ended
    open_ended: bool = False          # True  ⇒ still being received
    raw: str = ""                     # original text for this range
    # 863 $w on this field: the break between it and the next 863.  Set only
    # where the statement itself shows the break -- one run of a discontinuous
    # list to the next.  Two separate 866s may have a gap between them too, but
    # that is a reading of the record rather than of the statement, and is not
    # decided here.
    break_after: str = ""

    def __post_init__(self) -> None:
        self.align_boundaries()

    def align_boundaries(self) -> None:
        """
        Slide a boundary that omits its leading levels down to where it fits.

        Position in `enum` is the level, and for a range written out in full
        that is all anyone needs.  A range that states two levels at one end and
        one at the other breaks it: "v. 12 no. 1-no. 6" puts "no. 6" at position
        0, where the other end has "v. 12", and the 863 comes out "$a 12-6" --
        volume 12 to volume 6, a range that runs backwards and is not what the
        statement says.

        The captions settle it.  The end's "no." can only be the level the start
        also calls "no.", so an empty level is pushed in front of it and the two
        line up: "$a 12 $b 1-6".

        Only a boundary whose captions fit at exactly one offset is moved.  If
        they fit nowhere, or in more than one place, nothing is moved and the
        converter reports the values it cannot place -- guessing which level a
        value belongs to is the error this exists to prevent, and a wrong guess
        here is invisible in the output.

        Run at construction, and again by anything that fills the boundaries in
        afterwards -- the parser builds an empty range and populates it, so
        construction is too early there.  Running twice costs nothing: once the
        captions line up there is nothing left to move.
        """
        if self.end is None:
            return

        s_caps = [lvl.caption for lvl in self.start.enum]
        e_caps = [lvl.caption for lvl in self.end.enum]
        if not any(s_caps) or not any(e_caps):
            return                      # nothing captioned to align by

        if all(s is None or e is None or s == e
               for s, e in zip(s_caps, e_caps)):
            return                      # they already agree where both speak

        if len(e_caps) < len(s_caps):
            offset = _sole_offset(e_caps, s_caps)
            if offset:
                self.end.enum = [EnumLevel()] * offset + self.end.enum
        elif len(s_caps) < len(e_caps):
            offset = _sole_offset(s_caps, e_caps)
            if offset:
                self.start.enum = [EnumLevel()] * offset + self.start.enum

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
            if ec.day is not None:
                levels["day"] = True
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
            for key in ("year", "month", "day"):
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

# 863 $w, the break indicator: the code that says what the break before the next
# 863 is.  "g" is a gap -- parts lacking from the holdings, or a break whose
# cause is not known, which is the honest reading of a cataloguer writing
# "nos. 1, 3".  "n" is a non-gap break, for parts never published or a
# discontinuity in the numbering itself; nothing here can tell that apart from a
# gap, so nothing here writes it.
BREAK_GAP = "g"
BREAK_NON_GAP = "n"


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


# A year, optionally split across the turn of one: "1996", "1996/97",
# "1996/1997".  A serial whose winter issue straddles the new year is numbered
# that way as a matter of course, and MARC records the pair in 863 $i the same
# way it records a combined month in $j -- slash-joined, both halves in full.
_YEAR_TOKEN = r"\d{4}(?:\s*/\s*\d{2,4})?"
_SPLIT_YEAR_RE = re.compile(r"^(\d{4})\s*/\s*(\d{2,4})$")


def normalise_year(raw: Optional[str]) -> Optional[str]:
    """
    '1996' -> '1996';  '1996/97' -> '1996/1997';  '1999/00' -> '1999/2000'.

    The two-digit half takes the first year's century, and rolls forward when
    that would put it in the past: "1999/00" is 1999-2000, not 1999-1900.
    """
    if not raw:
        return raw
    m = _SPLIT_YEAR_RE.match(raw.strip())
    if not m:
        return raw.strip()
    first, second = m.group(1), m.group(2)
    if len(second) == 4:
        return f"{first}/{second}"
    full = int(first[:2] + second.zfill(2))
    if full < int(first):
        full += 100
    return f"{first}/{full}"


# Simpler pattern for year-only holdings (e.g. "1990" or "1990-1994")
_YEAR_ONLY_RE = re.compile(rf"^\s*({_YEAR_TOKEN})\s*$")

# Matches the start of a new range: a volume-level caption at the beginning
# e.g. "v.", "vol.", "volume" – but NOT "no.", "n.", "pt." etc.
_VOL_START_RE = re.compile(
    r"^\s*(?:v(?:ol(?:ume)?)?)\s*[.\s]", re.IGNORECASE
)
_YEAR_START_RE = re.compile(rf"^\s*{_YEAR_TOKEN}\s*(?:$|-)")

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


# ---------------------------------------------------------------------------
# Discontinuous lists
# ---------------------------------------------------------------------------
#
# "v. 19 nos. 1, 3, 5, 7-12 (Jan, Mar, May, Jul-Dec 1915)" is four runs of
# holdings with gaps between them, written the compact way a cataloguer writes
# them.  MARC 21 records gaps as separate 863s under one 853, so the four runs
# are four fields -- which is exactly what the converter already builds from a
# statement written out longhand:
#
#   v. 1 no. 1 (Jan 1990), v. 1 no. 3 (Mar 1990)
#     -> 863 $8 1.1 $a 1 $b 1 $i 1990 $j 01
#        863 $8 1.2 $a 1 $b 3 $i 1990 $j 03
#
# So the compact form is expanded into the longhand one and handed to the unit
# parser, rather than given a grammar of its own.  Everything the unit parser
# knows about captions, combined issues and seasons then applies unchanged.

# One item of a list: a number, a combined designation ("7/8"), or a run
# ("7-12").  An item carrying its own caption is a new statement, not a
# continuation of this one, and never reaches here -- _split_ranges() has
# already cut the statement there.
_LIST_VALUE = (r"\d+[a-zA-Z]?(?:\s*/\s*\d+[a-zA-Z]?)*"
               r"(?:\s*-\s*\d+[a-zA-Z]?(?:\s*/\s*\d+[a-zA-Z]?)*)?")
_LIST_ITEM_RE = re.compile(rf"^{_LIST_VALUE}$")

# The first item of a list, with everything before it: "v. 19 nos. " and "1".
# The prefix ends in a caption, because the caption is what every later item
# inherits.  A list with no caption anywhere -- "8,13,15,17,19,20-" -- is
# matched by _LIST_ITEM_RE instead and takes an empty prefix: nothing says what
# those numbers are, and nothing has to, because they are the most significant
# enumeration level and the 853 writes "(*)" for a level with no caption.
_LIST_HEAD_RE = re.compile(
    rf"""^(?P<prefix>.*?(?:{_CAPTION_ALT})\s*\.?\s*)
         (?P<item>{_LIST_VALUE})\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

# The chronology block at the very end of a statement.
_TRAILING_CHRON_RE = re.compile(r"\(\s*(?P<chron>[^()]*?)\s*\)\s*$")

# A chronology item that states only a year, or a run of them.
_BARE_YEAR_ITEM_RE = re.compile(rf"^{_YEAR_TOKEN}(?:\s*-\s*{_YEAR_TOKEN})?$")

# One year, which "1915/16" still is -- a single publication year written across
# the turn of one.  "1982-1994" is not, and the difference decides whether a
# chronology stated once can be given to every run of a list.
_SINGLE_YEAR_ITEM_RE = re.compile(rf"^{_YEAR_TOKEN}$")

_FIRST_INT_RE = re.compile(r"\d+")


def _split_top_level(text: str, sep: str = ",") -> List[str]:
    """Split on `sep`, ignoring any that falls inside brackets of either kind."""
    depth = 0
    parts: List[str] = []
    current: List[str] = []
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts]


def _chron_items(raw: str) -> Optional[List[str]]:
    """
    The chronology block read as a list, or None if it is not one.

    The list has to be homogeneous: months and seasons throughout, or years
    throughout.  A mixed one is how the American date convention writes a single
    date -- "(Apr 18, 1996)" splits into "Apr 18" and "1996" -- and reading that
    as two items would break one date into two holdings runs.
    """
    parts = [p for p in _split_top_level(raw) if p]
    if len(parts) < 2:
        return parts
    if all(p[:1].isalpha() for p in parts):
        return parts
    if all(_BARE_YEAR_ITEM_RE.match(p) for p in parts):
        return parts
    return None


def _carry_year_back(parts: List[str]) -> List[str]:
    """
    Give every chronology item the year it is written under.

    "(Jan, Mar, May, Jul-Dec 1915)" states 1915 once, at the end, for all four.
    "(Nov 1915, Jan 1916)" states one for each.  Reading right to left covers
    both: an item without a year belongs to the nearest year on its right.
    """
    carried: Optional[str] = None
    out: List[str] = []
    for part in reversed(parts):
        found = re.search(rf"\b{_YEAR_TOKEN}", part)
        if found:
            carried = found.group(0)
            out.append(part)
        elif carried:
            out.append(f"{part} {carried}")
        else:
            out.append(part)
    return list(reversed(out))


def _gap_after(item: str, nxt: str) -> str:
    """
    The 863 $w break indicator for the break between two items of a list.

    "g" is a gap break -- parts lacking, or a break whose cause is not known --
    which is what a cataloguer listing "nos. 1, 3" is recording.  Two items that
    run straight on ("nos. 1, 2") have no break to indicate, and nothing is
    written.  Numbering that cannot be read as integers is not evidence of
    continuity, so it is treated as a gap.
    """
    ends = _FIRST_INT_RE.findall(item)
    starts = _FIRST_INT_RE.findall(nxt)
    if ends and starts and int(starts[0]) == int(ends[-1]) + 1:
        return ""
    return BREAK_GAP


def _note_undistributable(warnings: Optional[List[str]], chron: str,
                          runs: int) -> None:
    """
    Record a chronology stated once for a list it cannot be shared across.

    A compressed 863 carries the dates of its own run.  A single year can be
    every run's year and is written to all of them; a range, or anything more
    specific than a year, belongs to the statement as a whole and to no
    particular run in it.  There is no notation for that, so it is named --
    which keeps it accounted for, and tells the cataloguer what to add by hand.
    """
    if warnings is None:
        return
    note = (
        f"'{chron}' was left out: it is stated once for all {runs} runs of this "
        f"statement, and it is not a single year that could be true of each of "
        f"them. Each 863 records the dates of its own run, and there is no way "
        f"to divide this one between them. The holdings themselves are "
        f"recorded; add the dates by hand if they matter."
    )
    if note not in warnings:
        warnings.append(note)


def _expand_distributed_list(text: str,
                             warnings: Optional[List[str]] = None,
                             ) -> Optional[tuple]:
    """
    Read a list of discontinuous runs, or None if the statement is not one.

    Returns what the list *is* -- the prefix every item inherits, the items
    themselves, a chronology for each, and whether the last one is still open --
    and leaves reading each run to the caller, which has two ways to do it
    depending on whether the list captions anything.

    "v. 19 nos. 1, 3, 5, 7-12 (Jan, Mar, May, Jul-Dec 1915)" becomes

        v. 19 nos. 1 (Jan 1915)
        v. 19 nos. 3 (Mar 1915)
        v. 19 nos. 5 (May 1915)
        v. 19 nos. 7-12 (Jul-Dec 1915)

    All of it or none of it.  The two lists have to be the same length, because
    pairing them is the whole claim being made: four issue runs against three
    months says the statement was not understood, and a converter that carried
    on would file holdings under the wrong dates.  A single bare year is the one
    exception -- "(1915)" is stated once for every run in the list and applies to
    all of them.
    """
    chron_raw = None
    head = text.strip()
    m = _TRAILING_CHRON_RE.search(head)
    if m:
        chron_raw = m.group("chron")
        head = head[:m.start()].strip()

    # "8,13,15,17,19,20-(1982-1994)" is still being received, and the hyphen
    # saying so is the last thing before the chronology.  Taken off here so the
    # final item parses as a plain number, and put back on the statement built
    # from it, which is where _parse_one_range looks for it.
    open_ended = bool(re.search(r"-\s*$", head))
    if open_ended:
        head = re.sub(r"-\s*$", "", head).strip()

    parts = [p for p in _split_top_level(head) if p]
    if len(parts) < 2:
        return None

    first = _LIST_HEAD_RE.match(parts[0])
    if first:
        prefix, first_item = first.group("prefix"), first.group("item")
    elif _LIST_ITEM_RE.match(parts[0]):
        prefix, first_item = "", parts[0]
    else:
        return None
    if not all(_LIST_ITEM_RE.match(p) for p in parts[1:]):
        return None

    items = [first_item] + parts[1:]

    chrons: List[Optional[str]] = [None] * len(items)
    if chron_raw:
        chron_parts = _chron_items(chron_raw)
        if chron_parts is None:
            return None
        if len(chron_parts) == 1 and _SINGLE_YEAR_ITEM_RE.match(chron_parts[0]):
            # "(1915)" is the year every run in the list falls in, stated once.
            chrons = [chron_parts[0]] * len(items)
        elif len(chron_parts) == len(items):
            chrons = list(_carry_year_back(chron_parts))
        elif len(chron_parts) == 1:
            # One chronology for several runs that is not a single year.
            # "(1982-1994)" spans the statement, not any one run in it, and
            # "(Jan 1915)" cannot be true of both no. 1 and no. 3.  Giving it to
            # each run would put twelve years on a single issue.  The
            # enumeration is unambiguous and is kept; the chronology is named.
            _note_undistributable(warnings, chron_raw.strip(), len(items))
        else:
            return None

    return prefix, items, chrons, open_ended


def is_distributed_list(text: str) -> bool:
    """
    Whether `text` lists several runs of holdings rather than describing one.

    Public because the Workbench needs the same answer the parser uses. A
    statement like this is more ranges than a confirmed pattern has roles to
    describe -- a pattern captures a fixed set of values and pairs them as one
    compressed range -- so the Workbench neither splits it into fragments for
    the confirm step nor lets a pattern claim it, and hands it to the parser
    whole.
    """
    return _expand_distributed_list(text) is not None


def _parse_distributed_list(text: str,
                            warnings: Optional[List[str]] = None,
                            ) -> Optional[List[HoldingsRange]]:
    """
    One HoldingsRange per run of a discontinuous list, or None if it is not one.

    Every expanded statement has to parse.  A list the parser can read four
    fifths of is the case this whole area exists to refuse: the 866 is removed
    once anything is written from it, so the fifth run would be deleted rather
    than recorded.
    """
    read = _expand_distributed_list(text, warnings)
    if read is None:
        return None
    prefix, items, chrons, open_ended = read

    ranges: List[HoldingsRange] = []
    for i, (item, chron) in enumerate(zip(items, chrons)):
        last = i == len(items) - 1

        if prefix:
            # Written back out longhand and handed to the unit parser, so
            # everything it knows about captions, combined issues and seasons
            # applies unchanged.
            stmt = f"{prefix}{item}" + (f" ({chron})" if chron else "")
            if last and open_ended:
                stmt += "-"
            hr = _parse_one_range(stmt, warnings)
            if not (hr.start.has_enum() or hr.start.has_chron()):
                return None
            hr.raw = stmt
        else:
            # No caption anywhere in the list.  There is nothing to parse on the
            # enumeration side -- _LIST_ITEM_RE has already established that the
            # item is a plain value -- and the unit parser refuses a lone
            # captionless number by design, because on its own it says nothing
            # about which level it is.  Inside a list it is not on its own: it
            # is the only enumeration level there is, and the 853 writes "(*)"
            # for a level with no caption rather than guessing at one.
            year, month, day = (_parse_chron(chron, warnings) if chron
                                else (None, None, None))
            hr = HoldingsRange(
                start=EnumChron(enum=[EnumLevel(value=item)],
                                year=year, month=month, day=day),
                open_ended=last and open_ended,
                raw=item,
            )
        ranges.append(hr)

    for i in range(len(ranges) - 1):
        ranges[i].break_after = _gap_after(items[i], items[i + 1])
    return ranges


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
        elif (ch == "/" and depth == 0
              and i > 0 and chars[i - 1].isspace()
              and i + 1 < len(chars) and chars[i + 1].isspace()):
            # A slash separates only when whitespace surrounds it.  A bare
            # slash carries meaning inside a statement -- combined issues
            # ("v.1/2"), combined months ("Jul./Aug."), split years
            # ("1990/91") -- and splitting on those would corrupt it.
            #
            # split_multi_range() in the detector has drawn this distinction
            # since 0.5.1; _split_ranges() never did, so
            # "v.1(1990)-v.3(1992) / v.5(1994)-v.8(1997)" reached _parse_unit()
            # as one unit and came out as "$a 1 $i 1990" -- one volume of the
            # eight it states.  None of the conditions below apply: unlike a
            # comma, a spaced slash is never part of a caption or a
            # designation, so there is nothing to disambiguate.
            candidates.append(i)
            segment_start = i + 1
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
                        ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Parse ONE chronology boundary (no range hyphen), e.g.:
      '1990'  '1990:Mar.'  'Spring 1990'  '1990 Spring'  'Mar. 1990'  'Jan'
      'Apr 18, 1996'  -> ('1996', '04', '18')
    Returns (year, month_code_or_text, day).
    """
    raw = raw.strip()
    if not raw:
        return None, None, None

    # Bare year, including a split one ("1996/97")
    m = re.match(rf"^({_YEAR_TOKEN})$", raw)
    if m:
        return normalise_year(m.group(1)), None, None

    # Mon D, YYYY -- a day-level date.  Every other alternative here wants the
    # year adjacent to the month, so "Apr 18, 1996" matched none of them and
    # the whole boundary was returned as (None, None): the month and the year
    # went with the day.  All three levels are kept now; 863 $k holds the day.
    m = re.match(rf"^([A-Za-z.]+)\s+(\d{{1,2}})\s*,?\s+({_YEAR_TOKEN})$", raw)
    if m and chron_unit_code(m.group(1)) is not None:
        return (normalise_year(m.group(3)), _chron_unit_value(m.group(1)),
                m.group(2).lstrip("0") or "0")

    # YYYY:Mon. or YYYY Season (year first)
    m = re.match(rf"({_YEAR_TOKEN})\s*[:\s]\s*([A-Za-z./]+(?:\s+[A-Za-z./]+)?)$", raw)
    if m:
        return normalise_year(m.group(1)), _chron_unit_value(m.group(2)), None

    # Mon. YYYY or Season YYYY (chron before year)
    m = re.match(rf"([A-Za-z./]+(?:\s+[A-Za-z./]+)?)\s*[:\s]\s*({_YEAR_TOKEN})$", raw)
    if m:
        return normalise_year(m.group(2)), _chron_unit_value(m.group(1)), None

    # Mon D -- a day-level date with the year supplied by the other boundary
    # or by the block it sits in, e.g. the 'Jan 28' in '[Jan 28-Dec 29]'.
    m = re.match(r"^([A-Za-z.]+)\s+(\d{1,2})$", raw)
    if m and chron_unit_code(m.group(1)) is not None:
        return None, _chron_unit_value(m.group(1)), m.group(2).lstrip("0") or "0"

    # Bare month/season name (year supplied by the other boundary,
    # e.g. the 'Jan' in 'Jan-Jun 1984')
    m = re.match(r"^([A-Za-z./]+)$", raw)
    if m:
        return None, _chron_unit_value(m.group(1)), None

    return None, None, None


def _parse_chron(raw: str,
                 warnings: Optional[List[str]] = None,
                 ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
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
    (01-12, seasons 21-24) for use in 863 $j; a day, when the statement gives
    one, goes to 863 $k.
    Returns (year, month, day).
    """
    raw = raw.strip()

    if "-" in raw:
        left, right = (p.strip() for p in raw.split("-", 1))
        l_year, l_month, l_day = _parse_chron_single(left, warnings)
        r_year, r_month, r_day = _parse_chron_single(right, warnings)

        # Year: share the right-hand year if the left boundary omits it.
        # Equal years collapse: the year is the most significant chronology
        # level, so there is nothing above it whose endpoints a repeat would
        # be pairing with.
        if l_year and r_year:
            year = l_year if l_year == r_year else f"{l_year}-{r_year}"
        else:
            year = l_year or r_year

        month = _pair_or_drop(l_month, r_month, raw, "month or season", warnings)
        # The day follows exactly the same rule, and for the same reason: a
        # lone '18' in $k pairs positionally with whatever $i and $j hold, so
        # it would claim a precision the other end of the range never gave.
        day = _pair_or_drop(l_day, r_day, raw, "day", warnings)

        if year or month or day:
            return year, month, day
        return raw, None, None  # unparseable: preserve raw so nothing is lost

    year, month, day = _parse_chron_single(raw, warnings)
    if year or month or day:
        return year, month, day

    # Give up - return raw as year string so the data is not dropped
    return raw, None, None


def _pair_or_drop(left: Optional[str], right: Optional[str], raw: str,
                  what: str, warnings: Optional[List[str]]) -> Optional[str]:
    """
    Join the two ends of one chronology level, or drop a value only one gives.

    Two ends naming the same month keep both -- "Jan 1956 - Jan 1957" is
    '01-01', not '01'.  Collapsing it lost the pairing with the years either
    side, leaving "$i 1956-1957 $j 01", which reads as one January spanning two
    years.

    One end naming a value and the other not -- "1981 - Sep 1996", "Aug
    1984-1985" -- cannot be recorded at all.  A reader pairs the subfields
    positionally, so "$i 1981-1996 $j 09" says the run *begins* in September
    1981, which the statement never claimed.  There is no notation for a
    chronology belonging to one end only, so the value is dropped and named.
    This is the only place that can tell the two cases apart: by the time the
    converter sees a lone '09' it cannot know whether the other end said the
    same thing or said nothing.
    """
    if left and right:
        return f"{left}-{right}"
    if not (left or right):
        return None
    if warnings is not None:
        lone = left or right
        note = (
            f"Only one end of '{raw}' gives a {what} ({lone}); with nothing at "
            "the other end it cannot be recorded as a range, so it was left out."
        )
        if note not in warnings:
            warnings.append(note)
    return None


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
        ec.year, ec.month, ec.day = _parse_chron(chron.group("chron_raw"), warnings)

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
        if end is None:
            # _parse_unit() refuses a unit it can only read part of, and says
            # so: "nothing was converted from this statement rather than
            # convert part of it".  Keeping the start made that untrue -- the
            # statement above wrote "$a 1 $i 1990" under a warning saying
            # nothing had been written, and was neither held nor flagged, so
            # nobody would have looked.  Refusing the range is what the
            # message has always claimed happens.
            return hr
        hr.start = start or EnumChron()
        hr.end = end

    hr.align_boundaries()
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
        if re.fullmatch(rf"\s*{_YEAR_TOKEN}\s*-\s*{_YEAR_TOKEN}\s*", text):
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


def _bracket_chron_unit(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    MARC chronology for one side of a bracketed group, as (month, day).

    'Jun 1'    -> ('06', '1')
    'Jul/Aug'  -> ('07/08', None)   (combined issue, via _chron_unit_value)
    'summer'   -> ('22', None)
    'Sum'      -> ('Sum', None)     (unrecognised: preserved, not dropped)
    """
    raw = raw.strip()
    if not raw:
        return None, None
    m = re.match(r"([A-Za-z]+(?:\s*/\s*[A-Za-z]+)*)\s*(\d{1,2})?", raw)
    if not m:
        return None, None
    month = _chron_unit_value(re.sub(r"\s*", "", m.group(1)))
    day = (m.group(2) or "").lstrip("0") or m.group(2)
    return month, day or None


def _parse_bracket_chron(raw: str) -> Tuple[Tuple[Optional[str], Optional[str]],
                                            Tuple[Optional[str], Optional[str]]]:
    """
    Parse a bracketed chronology group into ((start month, day), (end month, day)).

    '[Feb]'            -> (('02', None),  (None, None))
    '[Feb-Nov]'        -> (('02', None),  ('11', None))
    '[Jan 5-Jan 26]'   -> (('01', '5'),   ('01', '26'))
    '[Jul/Aug]'        -> (('07/08', None), (None, None))
    """
    raw = raw.strip()
    if not raw:
        return (None, None), (None, None)
    if "-" in raw:
        left, right = (p.strip() for p in raw.split("-", 1))
        return _bracket_chron_unit(left), _bracket_chron_unit(right)
    return _bracket_chron_unit(raw), (None, None)


def _drop_unfilled_top_level(ranges: List[HoldingsRange]) -> None:
    """
    Remove a placeholder level no block in the statement ever fills.

    The empty level exists to keep a block that omits its higher level in step
    with one that states it -- the two "2"s of
    "N1984: (2 (1))M1985: 2 (2 [summer])" are the same level and belong in the
    same subfield.  Where *no* block states it there is nothing to be in step
    with, and declaring it anyway would put a level in the 853 that the serial
    does not have: "1993: (1 [Feb])" would produce "$a (*) $b (*)" over an 863
    filling only $b.
    """
    if not any(len(hr.start.enum) > 1 for hr in ranges):
        return
    if any(hr.start.value_at(0) for hr in ranges):
        return
    for hr in ranges:
        if hr.start.enum and hr.start.enum[0].value is None:
            hr.start.enum = hr.start.enum[1:]


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
            (c_start, d_start), (c_end, d_end) = \
                _parse_bracket_chron(im.group("chron") or "")

            if not any([vol, issue, year, c_start]):
                continue

            # Positional, and it always was: a number *before* the parens is
            # the higher level and numbers *inside* are the lower one.
            #
            # Which means the lower one keeps its position when the higher is
            # absent.  Appending both in turn shifted it up instead, so
            # "N1984: (2 (1))M1985: 2 (2 [summer])" put the 1984 issue in $a and
            # the 1985 issue in $b -- the same level of the same serial in two
            # subfields, under an 853 that then read "$a no. $b no.", two levels
            # with one name.  An empty level holds the place the block omits.
            #
            # Neither level is captioned, because the format names neither.  It
            # is positional notation; reading "volume" and "issue" out of it was
            # the tool supplying two words the record never used, and the 853
            # writes NO_CAPTION for a level nobody has named.  A cataloguer who
            # knows this house format can set the captions once in the settings,
            # which is a stated choice rather than a hidden default.
            enum: List[EnumLevel] = []
            if vol:
                enum.append(EnumLevel(value=vol))
            if issue:
                if not vol:
                    enum.append(EnumLevel())
                enum.append(EnumLevel(value=issue))
            start = EnumChron(enum=enum, year=year, month=c_start, day=d_start)
            # The end boundary exists when either chronology level differs:
            # "[Jan 5-Jan 26]" is one month and two days, and dropping the end
            # because the months match would lose the second day.
            end = (EnumChron(month=c_end, day=d_end)
                   if (c_end and c_end != c_start) or (d_end and d_end != d_start)
                   else None)
            result.ranges.append(
                HoldingsRange(start=start, end=end, raw=item or body.strip())
            )

    if result.ranges:
        _drop_unfilled_top_level(result.ranges)
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

        # A segment listing several discontinuous runs is several ranges, and
        # MARC records them as several 863s.  Tried before the unit parser
        # because the unit parser reads the first run and refuses the rest.
        listed = _parse_distributed_list(seg, seg_notes)
        if listed is not None:
            notes.extend(w for w in seg_notes if w not in notes)
            result.ranges.extend(listed)
            continue

        seg_notes = []
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
