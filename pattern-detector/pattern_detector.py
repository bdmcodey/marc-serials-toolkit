"""
pattern_detector.py
-------------------
Detects structural patterns in MARC 866 textual holdings statements and
generates named-capture-group regular expressions for each pattern cluster.

Fuzzy clustering: caption variant forms ("v.", "Vol.", "volume") all map to
the same VOL_CAP token kind, so they land in the same pattern group.
The generated regex uses alternation to match all observed forms.

Public API
----------
    from pattern_detector import detect_patterns, split_multi_range, PatternGroup

    groups = detect_patterns([
        "v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)",
        "Vol. 1, No. 1 (Spring 1990)-Vol. 5, No. 4 (Winter 1994)",
        "v.6(1995)-",
    ])
    for g in groups:
        print(g.human_label)
        print(g.regex)
        print(g.named_groups)
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


# ── Token kind constants ──────────────────────────────────────────────────────

VOL_CAP     = "VOL_CAP"
ISS_CAP     = "ISS_CAP"
PT_CAP      = "PT_CAP"
YEAR        = "YEAR"
MON         = "MON"
SEASON      = "SEASON"
NUMBER      = "NUMBER"
PAREN_OPEN  = "PAREN_OPEN"
PAREN_CLOSE = "PAREN_CLOSE"
SEP_COLON   = "SEP_COLON"
SEP_HYPHEN  = "SEP_HYPHEN"
SEP_COMMA   = "SEP_COMMA"
SPACE       = "SPACE"
UNKNOWN     = "UNKNOWN"

_CAPTION_KINDS = {VOL_CAP, ISS_CAP, PT_CAP}
_VALUE_KINDS   = {YEAR, MON, SEASON, NUMBER}

# Clusters longer than this (in collapsed tokens) are reported as a finding
# rather than turned into a regex.
#
# Calibrated against two real MARC extracts (52 and 116 statements).  Real
# statements cost 15–45 regex characters per token — month alternations alone
# run ~180 characters — so the ceiling is set by /api/test-regex, which refuses
# any regex over 2,000 characters: above ~45 tokens this module would emit
# patterns the tool's own Test button rejects.  At 40 the longest generated
# regex observed was 1,470 characters.
#
# Everything flagged at this level was a singleton multi-year run-on
# ("1977: (46[Jul], 48-51[Sep-Dec])1978: …") — 45 tokens and up.  Nothing that
# currently produces a working regex is suppressed, which keeps the guard
# permissive toward pattern shapes not present in those samples.
MAX_PATTERN_TOKENS = 40

# General month/season patterns used in generated regex output —
# broad enough to match any standard form, not just the forms observed.
_MON_RE = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May"
    r"|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?"
    r"|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
_SEASON_RE = r"(?:Spring|Summer|Fall|Autumn|Winter)"


# ── Token dataclass ───────────────────────────────────────────────────────────

@dataclass
class Token:
    kind: str
    raw: str   # original text as found in the input


# ── Tokenizer ─────────────────────────────────────────────────────────────────
#
# Pattern ordering matters: first match wins per position.
#   - YEAR before NUMBER  → "1990" → YEAR not NUMBER
#   - SEASON/MON before ISS_CAP → "Nov." → MON not ISS_CAP("no") + garbage
#   - VOL_CAP/PT_CAP before ISS_CAP → no ambiguity on "v", "pt"
#
_TOK_RE = re.compile(
    # Volume caption  — v. | vol. | volume | v
    r"(?P<VOL_CAP>\bv(?:ol(?:ume)?)?\.?)"
    # Part caption    — pt. | part
    r"|(?P<PT_CAP>\b(?:pt|part)\.?)"
    # Four-digit year — 1800–2099 range, must precede NUMBER
    r"|(?P<YEAR>\b(?:1[89]|20)\d{2}\b)"
    # Season names    — word-boundary on both ends to avoid "springtime"
    r"|(?P<SEASON>\b(?:spring|summer|fall|autumn|winter)\b)"
    # Month names / abbreviations (must precede ISS_CAP to protect "Nov.", etc.)
    r"|(?P<MON>\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?"
    r"|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?"
    r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?)"
    # Issue caption   — no. | nr. | num. | number | iss. | issue
    r"|(?P<ISS_CAP>\b(?:no|nr|num(?:ber)?|iss(?:ue)?)\.?)"
    # Generic number (possibly with trailing letter: "4a", "12b")
    r"|(?P<NUMBER>\d+[a-zA-Z]?)"
    r"|(?P<PAREN_OPEN>\()"
    r"|(?P<PAREN_CLOSE>\))"
    r"|(?P<SEP_COLON>:)"
    r"|(?P<SEP_HYPHEN>-)"
    r"|(?P<SEP_COMMA>,)"
    r"|(?P<SPACE>\s+)"
    r"|(?P<UNKNOWN>.)",
    re.IGNORECASE,
)


def tokenize(text: str) -> list[Token]:
    """Tokenize a holdings statement into a list of Token objects."""
    return [Token(kind=m.lastgroup, raw=m.group()) for m in _TOK_RE.finditer(text)]


def _strip_spaces(tokens: list[Token]) -> list[Token]:
    """Drop all SPACE tokens; spacing is handled with \\s* in generated regexes."""
    return [t for t in tokens if t.kind != SPACE]


def _collapse_unknown_runs(tokens: list[Token]) -> list[Token]:
    """
    Merge each maximal run of UNKNOWN tokens into a single UNKNOWN token.

    The tokenizer's last alternative is (?P<UNKNOWN>.), so free-text noise
    ("Library has:", "[lacks v.3]") arrives as one token per character.  Left
    alone that produces one regex fragment per character — bespoke output that
    only ever matches the record it came from.

    A run is a maximal sequence of UNKNOWN and SPACE tokens that both *starts*
    and *ends* with UNKNOWN; interior SPACE is absorbed so the merged `raw`
    spans the original text exactly.  Leading/trailing SPACE stays outside the
    run and is dropped by _strip_spaces() as before.

    Must be applied before _strip_spaces() — the interior spaces are what make
    the merged length match the source text.
    """
    out: list[Token] = []
    i, n = 0, len(tokens)
    while i < n:
        if tokens[i].kind != UNKNOWN:
            out.append(tokens[i])
            i += 1
            continue
        # Scan forward over UNKNOWN/SPACE, remembering the last UNKNOWN seen
        # so the run never ends on absorbed whitespace.
        j, last = i, i
        while j < n and tokens[j].kind in (UNKNOWN, SPACE):
            if tokens[j].kind == UNKNOWN:
                last = j
            j += 1
        out.append(Token(kind=UNKNOWN, raw="".join(t.raw for t in tokens[i:last + 1])))
        i = last + 1
    return out


def get_signature(text: str) -> str:
    """
    Compute the fuzzy pattern signature for a holdings string.

    The signature is the pipe-separated sequence of token kinds with
    SPACE tokens removed.  Because all caption variants (v., Vol., volume)
    map to VOL_CAP, two statements that differ only in capitalisation or
    abbreviation style will share the same signature and be clustered together.

    Consecutive UNKNOWN tokens are collapsed first, so two statements that
    differ only in the length of a free-text note ("Library has: v.1(1990)"
    vs "[lacks] v.1(1990)") also share a signature.
    """
    return "|".join(
        t.kind for t in _collapse_unknown_runs(tokenize(text)) if t.kind != SPACE
    )


# ── Range separator detection ─────────────────────────────────────────────────

def _find_range_sep(stripped: list[Token]) -> Optional[int]:
    """
    Return the index within `stripped` of the top-level SEP_HYPHEN that
    separates the start unit from the end unit (e.g. the "-" in "v.1(1990)-v.5(1994)").

    Rules:
    • Must be at paren depth 0 (not inside a chronology block).
    • Must be followed by at least one more token (not the trailing open-ended hyphen).
    • Must actually look like a unit separator, not a range *within* one caption
      level.  The hyphen in "v.1-5(1990-1994)" joins two volumes; it does not
      divide the statement into a start unit and an end unit, and treating it as
      though it did put every later value on the wrong side of the range.

    The third rule mirrors holdings_parser._smart_split_range, which has always
    made the same distinction: a digit-to-digit hyphen is a compressed range at
    one level, so a separator needs a closing paren before it, a caption after
    it, or years on both sides (the bare "1990-1994" form).

    Returns None for open-ended statements ("v.6(1995)-"), for single-unit
    statements, and for statements whose only top-level hyphen is a compressed
    range -- all three genuinely have no start/end division.
    """
    depth = 0
    for i, tok in enumerate(stripped):
        if tok.kind == PAREN_OPEN:
            depth += 1
        elif tok.kind == PAREN_CLOSE:
            depth -= 1
        elif tok.kind == SEP_HYPHEN and depth == 0 and i + 1 < len(stripped):
            prev = stripped[i - 1] if i else None
            nxt = stripped[i + 1]
            if prev is not None and prev.kind == PAREN_CLOSE:
                return i                      # "...(1990)-v.5(1994)"
            if nxt.kind in _CAPTION_KINDS:
                return i                      # "...-v.5", "...-no.4"
            if prev is not None and prev.kind == YEAR and nxt.kind == YEAR:
                return i                      # bare "1990-1994"
    return None


def _is_open_ended(stripped: list[Token]) -> bool:
    """True when the statement ends with a bare hyphen (currently received)."""
    return bool(stripped) and stripped[-1].kind == SEP_HYPHEN


# ── Regex-building helpers ────────────────────────────────────────────────────

def _alt_or_literal(vals: list[str]) -> str:
    """
    From a list of raw string values, produce either a single re.escape(val)
    or a (?:...|...) alternation sorted longest-first for greedy correctness.
    """
    unique = sorted(set(v.strip() for v in vals if v.strip()), key=len, reverse=True)
    if not unique:
        return r"\S+"
    if len(unique) == 1:
        return re.escape(unique[0])
    return "(?:" + "|".join(re.escape(v) for v in unique) + ")"


def _unknown_bound(n: int) -> int:
    """
    Upper bound for a collapsed UNKNOWN run of `n` characters, rounded up to
    the next multiple of 8 (minimum 8).  The headroom lets a similar note of
    slightly different length match too, instead of pinning the pattern to the
    exact free text observed.
    """
    return max(8, -(-n // 8) * 8)


def _unique_name(base: str, used: set[str]) -> str:
    """Return `base` if not yet used, else `base_2`, `base_3`, …"""
    if base not in used:
        used.add(base)
        return base
    n = 2
    while f"{base}_{n}" in used:
        n += 1
    name = f"{base}_{n}"
    used.add(name)
    return name


# ── Compact human-readable label ─────────────────────────────────────────────

_CAP_SHORT = {VOL_CAP: "VOL", ISS_CAP: "ISS", PT_CAP: "PT"}
_VAL_SHORT = {YEAR: "YEAR", MON: "MON", SEASON: "SEASON"}
_SEP_SHORT = {SEP_COLON: ":", SEP_COMMA: ",", PAREN_OPEN: "(", PAREN_CLOSE: ")"}


def _compact_label(stripped: list[Token], range_sep_idx: Optional[int]) -> str:
    """
    Build a short human-readable description from the token sequence, e.g.:
      "VOL:ISS(YEAR:MON) — VOL:ISS(YEAR:MON)"
      "VOL-VOL(YEAR-YEAR)"
      "YEAR — YEAR"
    Caption + NUMBER pairs are collapsed to just "VOL", "ISS", "PT".

    A compressed range keeps its hyphen and repeats the caption governing it:
    the second number in "v.1-5" is a volume, so the label says so rather than
    dropping the hyphen and reading "VOL#".  This is the heading a cataloguer
    picks a pattern out by, so it has to describe the shape they are looking at.
    """
    parts: list[str] = []
    i = 0
    last_cap: Optional[str] = None    # caption governing the current range
    while i < len(stripped):
        tok = stripped[i]
        if i == range_sep_idx:
            parts.append(" \u2014 ")       # em-dash
            last_cap = None               # captions do not cross the separator
            i += 1
            continue
        kind = tok.kind
        if kind in _CAP_SHORT:
            last_cap = _CAP_SHORT[kind]
            parts.append(last_cap)
            # Silently consume the following NUMBER (it's implied)
            if i + 1 < len(stripped) and stripped[i + 1].kind == NUMBER:
                i += 2
                continue
        elif kind == NUMBER:
            # The far side of a compressed range inherits the caption before it;
            # a number with no caption anywhere is genuinely just a number.
            following_hyphen = i and stripped[i - 1].kind == SEP_HYPHEN
            parts.append(last_cap if (following_hyphen and last_cap) else "#")
        elif kind in _VAL_SHORT:
            parts.append(_VAL_SHORT[kind])
        elif kind in _SEP_SHORT:
            parts.append(_SEP_SHORT[kind])
        elif kind == SEP_HYPHEN:
            # Keep it: an intra-level range ("v.1-5", "1990-1994" inside parens)
            # is part of the shape, and eliding it ran the two values together.
            parts.append("\u2013" if i == len(stripped) - 1 else "-")
        i += 1
    return "".join(parts)


# ── Core regex builder ────────────────────────────────────────────────────────

def _build_regex(
    all_stripped: list[list[Token]],
) -> tuple[str, list[str], dict[str, list[str]]]:
    """
    Given a list of stripped-token lists that all share the same signature,
    build a Python named-group regex that matches all of them.

    Returns
    -------
    regex         : the pattern string (use re.compile(regex, re.IGNORECASE))
    named_groups  : ordered list of capture-group names (["start_vol", ...])
    cap_variants  : observed caption forms per level, e.g. {"vol": ["v.", "Vol."]}
    """
    template = all_stripped[0]
    n = len(template)

    # Collect all raw values seen at each token position across all statements
    pos_vals: list[list[str]] = [
        [toks[i].raw for toks in all_stripped if i < len(toks)]
        for i in range(n)
    ]

    range_sep_idx = _find_range_sep(template)
    open_ended    = _is_open_ended(template)

    parts: list[str]               = []
    named_groups: list[str]        = []
    cap_variants: dict[str, list]  = {}
    used_names: set[str]           = set()

    prev_cap: Optional[str] = None    # last caption kind: "vol" | "iss" | "part"

    # Which boundary a value sits on is a property of its own level, not of the
    # statement as a whole.  "v.1-5(1990-1994)" has no single point dividing a
    # start half from an end half -- it has two compressed ranges, one per
    # level, each with its own start and end.  Counting per level describes both
    # that shape and "v.1(1990)-v.5(1994)", where the two happen to coincide.
    #
    # A third value at one level has no boundary left to take, so it falls back
    # to _unique_name's suffix ("end_year_2") and is treated as unencodable.
    level_seen: dict[str, int] = {}

    def boundary_name(slot: str) -> str:
        n = level_seen.get(slot, 0)
        level_seen[slot] = n + 1
        return _unique_name(f"{'start' if n == 0 else 'end'}_{slot}", used_names)

    for i, tok in enumerate(template):
        kind  = tok.kind
        vals  = pos_vals[i]
        unique = sorted(set(v.strip() for v in vals if v.strip()), key=len, reverse=True)

        # ── Range separator ───────────────────────────────────────────────────
        if i == range_sep_idx:
            # Captions do not carry across the separator: the "5" in
            # "v.1(1990)-5(1994)" is not covered by the "v." before the hyphen.
            prev_cap = None
            parts.append(r"\s*-\s*")
            continue

        # ── Caption tokens (VOL_CAP / ISS_CAP / PT_CAP) ──────────────────────
        if kind == VOL_CAP:
            prev_cap = "vol"
            parts.append(_alt_or_literal(unique))
            parts.append(r"\s*")
            _record_variants(cap_variants, "vol", unique)
            continue

        if kind == ISS_CAP:
            prev_cap = "iss"
            parts.append(_alt_or_literal(unique))
            parts.append(r"\s*")
            _record_variants(cap_variants, "iss", unique)
            continue

        if kind == PT_CAP:
            prev_cap = "part"
            parts.append(_alt_or_literal(unique))
            parts.append(r"\s*")
            _record_variants(cap_variants, "part", unique)
            continue

        # ── Value tokens — named capture groups ───────────────────────────────
        if kind == NUMBER:
            name = boundary_name(prev_cap or "num")
            named_groups.append(name)
            parts.append(rf"(?P<{name}>\d+[a-zA-Z]?)")
            parts.append(r"\s*")
            continue

        if kind == YEAR:
            name = boundary_name("year")
            named_groups.append(name)
            # Allow 4-digit years in the realistic range; keep flexible
            parts.append(rf"(?P<{name}>(?:1[89]|20)\d{{2}})")
            parts.append(r"\s*")
            continue

        if kind == MON:
            name = boundary_name("month")
            named_groups.append(name)
            # Use the general month pattern so future statements with any
            # standard month abbreviation will also be matched.
            parts.append(rf"(?P<{name}>{_MON_RE})")
            parts.append(r"\s*")
            continue

        if kind == SEASON:
            name = boundary_name("month")
            named_groups.append(name)
            parts.append(rf"(?P<{name}>{_SEASON_RE})")
            parts.append(r"\s*")
            continue

        # ── Structural / punctuation tokens ───────────────────────────────────
        if kind == PAREN_OPEN:
            parts.append(r"\(\s*")
            continue

        if kind == PAREN_CLOSE:
            parts.append(r"\s*\)")
            parts.append(r"\s*")
            continue

        if kind == SEP_COLON:
            parts.append(r"\s*:\s*")
            continue

        if kind == SEP_HYPHEN:
            # Hyphens at this point are internal to a chronology block
            # (depth > 0 when the range_sep was already handled) or part of
            # a compressed enumeration range (v.1-5).  Allow surrounding
            # whitespace: cataloguers write both "(Nov/Dec 2008-May 2010)"
            # and "(Winter/Spring 1994 - Spring/Summer 1999)".
            parts.append(r"\s*-\s*")
            continue

        if kind == SEP_COMMA:
            parts.append(r",\s*")
            continue

        # ── Unrecognised free text — one bounded fragment per run ─────────────
        if kind == UNKNOWN:
            # Runs are pre-collapsed, so `vals` holds whole note spans rather
            # than single characters.  A bounded lazy wildcard generalises to
            # other notes of similar length; the trailing \s* covers the SPACE
            # token that _strip_spaces() removed after the run.
            longest = max((len(v) for v in vals if v), default=1)
            parts.append(rf".{{1,{_unknown_bound(longest)}}}?")
            parts.append(r"\s*")
            continue

        # ── Fallback: escape whatever is left ─────────────────────────────────
        parts.append(_alt_or_literal(unique))

    return "".join(parts), named_groups, cap_variants


def _record_variants(
    cap_variants: dict[str, list[str]],
    key: str,
    unique: list[str],
) -> None:
    """Update cap_variants[key] with any new values from unique."""
    existing = cap_variants.setdefault(key, [])
    for v in unique:
        if v not in existing:
            existing.append(v)


# ── Regex validator ───────────────────────────────────────────────────────────

def _validate(
    regex: str,
    statements: list[str],
) -> tuple[float, list[str], list[str]]:
    """
    Test a regex against every statement using re.fullmatch (IGNORECASE).

    Returns (match_rate 0.0–1.0, matched_list, failed_list).
    Falls back to re.search on fullmatch failure so partially-parsed
    multi-range strings still report a hit.
    """
    matched, failed = [], []
    try:
        compiled = re.compile(regex, re.IGNORECASE)
    except re.error:
        return 0.0, [], list(statements)

    for s in statements:
        hit = compiled.fullmatch(s.strip()) or compiled.search(s.strip())
        (matched if hit else failed).append(s)

    rate = len(matched) / len(statements) if statements else 1.0
    return rate, matched, failed


# ── PatternGroup dataclass ────────────────────────────────────────────────────

@dataclass
class PatternGroup:
    signature: str                          # raw token-kind sequence
    human_label: str                        # compact display label
    count: int                              # number of statements
    examples: list[str]                     # every statement in the cluster
    regex: str                              # generated Python regex
    named_groups: list[str]                 # ordered group names
    match_rate: float                       # 0.0–1.0
    matched: list[str]
    failed: list[str]
    caption_variants: dict[str, list[str]]  # e.g. {"vol": ["v.", "Vol."]}
    is_open_ended: bool
    token_count: int = 0                    # collapsed structural tokens
    too_complex: bool = False               # over MAX_PATTERN_TOKENS; no regex

    def to_dict(self) -> dict:
        return {
            "signature":        self.signature,
            "human_label":      self.human_label,
            "count":            self.count,
            "examples":         self.examples,
            "regex":            self.regex,
            "named_groups":     self.named_groups,
            "match_rate":       round(self.match_rate, 4),
            "matched_count":    len(self.matched),
            "failed":           self.failed,
            "caption_variants": self.caption_variants,
            "is_open_ended":    self.is_open_ended,
            "token_count":      self.token_count,
            "too_complex":      self.too_complex,
        }


# ── Public API ────────────────────────────────────────────────────────────────

def detect_patterns(statements: list[str]) -> list[PatternGroup]:
    """
    Cluster a collection of 866 $a strings by structural pattern and
    return one PatternGroup per cluster, sorted by count descending.

    Parameters
    ----------
    statements : list of holdings strings, already de-duped and trimmed.
                 Multi-range strings should be pre-split with split_multi_range()
                 if sub-ranges should be analysed individually.
    """
    if not statements:
        return []

    # ── Cluster by fuzzy signature ────────────────────────────────────────────
    clusters: dict[str, list[str]] = defaultdict(list)
    for stmt in statements:
        s = stmt.strip()
        if not s:
            continue
        clusters[get_signature(s)].append(s)

    groups: list[PatternGroup] = []

    for sig, members in clusters.items():
        all_stripped = [
            _strip_spaces(_collapse_unknown_runs(tokenize(m))) for m in members
        ]
        template     = all_stripped[0]

        range_sep_idx = _find_range_sep(template)
        open_ended    = _is_open_ended(template)
        label         = _compact_label(template, range_sep_idx)

        # Guard *before* generating: a cluster this long yields a regex nobody
        # can read or edit, and is almost always a one-off rather than a real
        # pattern.  Report it as a finding instead of emitting the regex.
        if len(template) > MAX_PATTERN_TOKENS:
            groups.append(PatternGroup(
                signature        = sig,
                human_label      = (label or sig)[:80],
                count            = len(members),
                examples         = members,
                regex            = "",
                named_groups     = [],
                match_rate       = 0.0,
                matched          = [],
                failed           = [],
                caption_variants = {},
                is_open_ended    = open_ended,
                token_count      = len(template),
                too_complex      = True,
            ))
            continue

        regex, named_groups, cap_variants = _build_regex(all_stripped)
        match_rate, matched, failed       = _validate(regex, members)

        groups.append(PatternGroup(
            signature        = sig,
            human_label      = label or sig[:80],
            count            = len(members),
            examples         = members,
            regex            = regex,
            named_groups     = named_groups,
            match_rate       = match_rate,
            matched          = matched,
            failed           = failed,
            caption_variants = cap_variants,
            is_open_ended    = open_ended,
            token_count      = len(template),
        ))

    groups.sort(key=lambda g: g.count, reverse=True)
    return groups


def split_multi_range(text: str) -> list[str]:
    """
    Split a holdings string that contains multiple comma-, semicolon- or
    slash-separated ranges into individual range strings, ignoring separators
    that fall inside parentheses.

    A slash separates only when whitespace surrounds it.  A bare slash carries
    meaning inside a statement — combined issues (v.1/2), split years
    (1990/91) — and splitting on those would corrupt the statement.

    Example
    -------
    "v.1(1990)-v.3(1992), v.5(1994)-"
    → ["v.1(1990)-v.3(1992)", "v.5(1994)-"]
    """
    depth   = 0
    parts:   list[str] = []
    current: list[str] = []

    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif depth == 0 and (
            ch in (",", ";")
            or (ch == "/"
                and i > 0            and text[i - 1].isspace()
                and i + 1 < len(text) and text[i + 1].isspace())
        ):
            seg = "".join(current).strip()
            if seg:
                parts.append(seg)
            current = []
        else:
            current.append(ch)

    seg = "".join(current).strip()
    if seg:
        parts.append(seg)

    return parts if parts else [text.strip()]
