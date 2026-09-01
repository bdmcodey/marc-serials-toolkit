"""
Run data/textual_holdings_corpus.txt through the parser, the converter and the
pattern detector, and report where they fall short.

This is a *reporting* script, not a test. It changes nothing and asserts
nothing; it prints what the three engines currently do with 112 real 866 $a
statements so that CORPUS-FINDINGS.md can be checked against the code rather
than remembered. Every count in that file comes from here.

    python scripts/corpus_report.py             # the summary
    python scripts/corpus_report.py --detail    # plus every affected statement
    python scripts/corpus_report.py --drift     # only tags that no longer hold

Three things are measured, because a statement can fail in three different ways
and only the first is visible from the outside:

  1. Refusal      -- no 853/863 is produced. Loud, and already surfaced to the
                     cataloguer as "held for review".
  2. Silent loss  -- fields are produced, but a number or a month present in the
                     statement appears nowhere in them. This is the dangerous
                     class: the Converter removes the source 866 by default once
                     anything has been written from it, so a dropped value is
                     gone from the record with nothing to say so.
  3. Fragmentation -- the detector clusters statements a cataloguer would call
                     one pattern into several, each needing its own
                     confirmation. Nothing is wrong with the output; the tool is
                     just asking the same question repeatedly.

The Workbench section additionally re-checks a fixed defect rather than only
measuring an open one: D17 let a confirmed pattern claim a substring of a
statement and discard the rest, and the report asks the real bridge, on every
statement some other cluster's regex partly matches, whether it would still
convert. It says REGRESSION if any does.

Silent loss is found three ways, because no one of them sees everything:

  * a value audit, comparing the digits and month/season names in the statement
    against the digits in the generated fields. Deliberately conservative -- a
    dropped value that happens to coincide with another value already in the
    output is not counted -- so it under-reports and never over-reports;
  * an end-boundary check, for enumeration that only the end of a range states
    ("v. 1 - v. 55 no. 3"), which the value audit misses whenever the same digit
    appears elsewhere in the field;
  * a coded-subfield check, for letters written into a chronology subfield the
    853 declares as (month) or (season). Nothing is dropped there -- the
    opposite: text that is not a chronology code reaches a subfield that may
    hold nothing else.
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "data" / "textual_holdings_corpus.txt"

# The engines import their siblings by bare name, exactly as tests/conftest.py
# explains, so their directories have to be on the path before importing.
for _d in (REPO_ROOT / "converter", REPO_ROOT / "pattern-detector",
           REPO_ROOT / "workbench"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from holdings_parser import parse_866, MARC_CHRON_CODES   # noqa: E402
from marc_converter import convert_holdings, read_853_slots  # noqa: E402
from pattern_detector import detect_patterns, get_signature  # noqa: E402


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------

SECTION_RE = re.compile(r"^\[section:\s*(?P<name>[^\]]+)\]\s*$")


class Entry:
    """One corpus line: the statement, its section, and its recorded outcome."""

    __slots__ = ("statement", "section", "status", "defect", "occurrences", "line")

    def __init__(self, statement, section, status, defect, occurrences, line):
        self.statement = statement
        self.section = section
        self.status = status          # "" | loss | fail | warn | known
        self.defect = defect          # "" | D1 .. Dn
        self.occurrences = occurrences
        self.line = line

    @property
    def tag(self) -> str:
        return f"{self.status}:{self.defect}" if self.status else ""

    def __repr__(self) -> str:      # pragma: no cover - debugging aid
        return f"<Entry {self.statement!r} {self.tag}>"


def load_corpus(path: Path = CORPUS) -> list[Entry]:
    """
    Parse the corpus file into Entry objects.

    A line is a comment when it starts with "#", a section header when it looks
    like "[section: name]", and otherwise a statement whose text runs up to an
    optional trailing "  # ..." annotation. No statement in the corpus contains
    "#", which is what makes that split safe -- see the file's own header.
    """
    entries: list[Entry] = []
    section = "(none)"

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = SECTION_RE.match(line.strip())
        if m:
            section = m.group("name").strip()
            continue

        statement, _, annotation = line.partition("#")
        statement = statement.strip()
        if not statement:
            continue

        occurrences = 1
        occ = re.search(r"\[x(\d+)\]", annotation)
        if occ:
            occurrences = int(occ.group(1))

        status = defect = ""
        tag = re.search(r"\b(loss|fail|warn|known):(D\d+)\b", annotation)
        if tag:
            status, defect = tag.group(1), tag.group(2)

        entries.append(Entry(statement, section, status, defect, occurrences, lineno))

    return entries


# ---------------------------------------------------------------------------
# Per-statement audit
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z]{3,}")


def _asserted_values(statement: str) -> tuple[list[str], list[str]]:
    """Numbers, and chronology codes for month/season words, present in the text."""
    numbers = re.findall(r"\d+", statement)
    chron = []
    for word in _WORD_RE.findall(statement):
        code = MARC_CHRON_CODES.get(word.lower().rstrip("."))
        if code:
            chron.append(code)
    return numbers, chron


class Outcome:
    """What the parser and converter actually did with one statement."""

    __slots__ = ("entry", "result", "conversion", "status", "dropped_numbers",
                 "dropped_chron", "undeclared", "end_only", "uncoded",
                 "collapsed", "phantom_start")

    def __init__(self, entry: Entry):
        self.entry = entry
        self.result = parse_866(entry.statement)
        self.conversion = convert_holdings(self.result)

        produced = bool(self.conversion.fields_863)
        if not produced:
            self.status = "fail"
        elif self.result.warnings:
            self.status = "warn"
        else:
            self.status = "ok"

        # Values the statement asserts that reach none of the generated fields.
        self.dropped_numbers: list[str] = []
        self.dropped_chron: list[str] = []
        if produced:
            rendered = " ".join(f.display() for f in self.conversion.all_fields())
            in_output = set(re.findall(r"\d+", rendered))
            numbers, chron = _asserted_values(entry.statement)
            self.dropped_numbers = [n for n in numbers if n not in in_output]
            self.dropped_chron = [c for c in chron if c not in in_output]
            if self.dropped_numbers or self.dropped_chron:
                self.status = "loss"

        # Levels the 853 declares that its own 863s never fill. A caption with
        # no value under it is a promise the record does not keep.
        #
        # Reported, but never on its own a loss: the block grammar makes one 863
        # per item and its items are legitimately sparse, so a missing subfield
        # there says nothing. end_only below is the check that is specific
        # enough to call it.
        self.undeclared: list[str] = []
        f853 = self.conversion.field_853
        if f853 and produced:
            declared = {sf.code for sf in f853.subfields if sf.code != "8"}
            for f863 in self.conversion.fields_863:
                got = {sf.code for sf in f863.subfields if sf.code != "8"}
                self.undeclared.extend(sorted(declared - got))

        # Enumeration stated only at the end of a range. "v. 1 - v. 55 no. 3"
        # says the run stops at issue 3, and _enum_value() returns None for a
        # level whose start is empty, so the subfield is not written at all --
        # which is what to look for. Testing whether the digit appears anywhere
        # in the field would not do: "v. 1-v. 2 no. 2" drops its issue, and a
        # "2" is still there in the "$a 1-2" beside it.
        self.end_only: list[str] = []
        slots = read_853_slots(f853) if f853 else {}
        for seq, hr in enumerate(self.result.ranges):
            if hr.end is None or seq >= len(self.conversion.fields_863):
                continue
            present = {sf.code for sf in self.conversion.fields_863[seq].subfields}
            for level in ("vol", "issue", "part"):
                if getattr(hr.start, level) is not None:
                    continue
                end_v = getattr(hr.end, level)
                if end_v is None:
                    continue
                code = slots.get(level)
                if code and code not in present:
                    self.end_only.append(f"{level}={end_v} (${code} never written)")

        # Text where a code belongs. The 853 labels these subfields (month) or
        # (season) and an 863 under them should hold 01-12 or 21-24; a season
        # the parser does not recognise ("Late Summer") or a designation that
        # is not chronology at all ("Buyers Guide") lands there as prose.
        self.uncoded: list[str] = []
        if f853 and produced:
            coded = {sf.code for sf in f853.subfields
                     if sf.value.strip().lower() in ("(month)", "(season)")}
            for f863 in self.conversion.fields_863:
                for sf in f863.subfields:
                    if sf.code in coded and re.search(r"[A-Za-z]", sf.value):
                        self.uncoded.append(f"${sf.code} {sf.value}")

        # A compressed 863 states the first part held and the last part held, so
        # every level has to be repeated at both ends of the range.
        # _enum_value() collapses "1-1" to "1" whenever the two endpoints are
        # equal, and that is lossy exactly when a *more significant* level does
        # range: "$a 41-43 $b 1" cannot be read back as v.41:no.1 - v.43:no.1,
        # because it equally describes issue 1 of each of volumes 41 to 43. The
        # endpoint pairing is what is destroyed.
        #
        # Collapsing is fine when nothing above the level ranges:
        # "v. 43 no. 6 - v. 43 no. 7" is fully recoverable from "$a 43 $b 6-7",
        # so only levels under a ranging one are flagged. Enumeration and
        # chronology are separate hierarchies, each ordered most significant
        # first.
        self.collapsed: list[str] = []
        for seq, hr in enumerate(self.result.ranges):
            if hr.end is None or seq >= len(self.conversion.fields_863):
                continue
            emitted = {sf.code: sf.value
                       for sf in self.conversion.fields_863[seq].subfields}
            for hierarchy in (("vol", "issue", "part"), ("year", "month")):
                ranged = False
                for level in hierarchy:
                    start_v = getattr(hr.start, level)
                    end_v = getattr(hr.end, level)
                    if start_v is None or end_v is None:
                        continue
                    if start_v != end_v:
                        ranged = True
                        continue
                    code = slots.get(level)
                    if ranged and code and emitted.get(code) == start_v:
                        self.collapsed.append(
                            f"${code} {start_v} should be {start_v}-{end_v}")

        # The mirror of end_only, for chronology. _build_863_for_range() falls
        # back to the end boundary when the start has no value -- which is right
        # for "v.1:no.1-v.2:no.4(1990-1991)", where the single chronology group
        # covers the whole range, and wrong for
        # "v. 1 no. 1 (1995)-v. 12 no. 4 (December 2006)", where December
        # belongs to the end alone. The field then asserts the run *begins* in
        # December 2006. Only the second shape is flagged: the range has its own
        # chronology group at each end, and only one of them named a month.
        self.phantom_start: list[str] = []
        for seq, hr in enumerate(self.result.ranges):
            if hr.end is None or seq >= len(self.conversion.fields_863):
                continue
            emitted = {sf.code: sf.value
                       for sf in self.conversion.fields_863[seq].subfields}
            for level in ("year", "month"):
                start_v, end_v = getattr(hr.start, level), getattr(hr.end, level)
                code = slots.get(level)
                if not code or start_v is not None or end_v is None:
                    continue
                # Only when the *other* chronology level proves both boundaries
                # carried a chronology group of their own.
                other = "month" if level == "year" else "year"
                if getattr(hr.start, other) is None:
                    continue
                if emitted.get(code) == end_v:
                    self.phantom_start.append(
                        f"${code} {end_v} is the end's {level}, written as the start's")

        if produced and (self.end_only or self.uncoded
                         or self.collapsed or self.phantom_start):
            self.status = "loss"

    @property
    def rendered(self) -> str:
        return " ".join(f.display() for f in self.conversion.all_fields()) or "(nothing)"


# ---------------------------------------------------------------------------
# Detector audit
# ---------------------------------------------------------------------------

def _coarse_signature(signature: str) -> str:
    """
    The signature a cataloguer would recognise, as opposed to the one the
    detector clusters on.

    Two differences are collapsed, and only two. A month and a season occupy the
    same slot in a statement, so MON and SEASON become one CHRON kind; and a
    combined chronology ("Jul/Aug", "Winter/Spring") is one value written with a
    slash, so a CHRON separated from a CHRON by the UNKNOWN the slash tokenises
    to becomes a single CHRON. Nothing else is touched -- this measures how far
    the detector's clustering is from a cataloguer's reading, and would prove
    nothing if it did the merging generously.
    """
    kinds = ["CHRON" if k in ("MON", "SEASON") else k for k in signature.split("|")]
    out: list[str] = []
    i = 0
    while i < len(kinds):
        if kinds[i] == "CHRON":
            j = i
            while j + 2 < len(kinds) and kinds[j + 1] == "UNKNOWN" and kinds[j + 2] == "CHRON":
                j += 2
            out.append("CHRON")
            i = j + 1
            continue
        out.append(kinds[i])
        i += 1
    return "|".join(out)


# ---------------------------------------------------------------------------
# The Workbench's confirmed-pattern path
# ---------------------------------------------------------------------------

def pattern_path_exposure(statements: list[str], groups: list) -> list[dict]:
    """
    Statements a *different* cluster's regex matches only part of.

    pattern_bridge.build_parse_result() used to match with

        m = compiled.fullmatch(seg) or compiled.search(seg)

    so a pattern that did not span the statement could still claim a substring,
    and every character outside that span was discarded with no warning.
    apply_patterns() takes the first pattern that matches, so the short, common
    pattern a cataloguer confirms first beat the longer correct one (D17).

    It now requires a full match. This measurement is kept for two reasons: it
    counts how often that rule is load-bearing, and `converted_on_partial`
    re-checks the actual behaviour against a real pattern, so the report fails
    loudly if the search fallback ever comes back. Each entry records the
    smallest span any cluster's regex would claim for that statement.
    """
    compiled = [(g.human_label, re.compile(g.regex, re.IGNORECASE), g.count)
                for g in groups if g.regex]

    exposure: list[dict] = []
    for statement in statements:
        worst = None
        for label, rx, size in compiled:
            if rx.fullmatch(statement):
                continue                       # spans it all: nothing discarded
            m = rx.search(statement)
            if m is None or m.end() == m.start():
                continue
            fraction = (m.end() - m.start()) / len(statement)
            if worst is None or fraction < worst["fraction"]:
                worst = {
                    "statement": statement,
                    "label": label,
                    "cluster_size": size,
                    "fraction": fraction,
                    "regex": rx,
                    "matched": statement[m.start():m.end()],
                    "discarded": (statement[:m.start()] + " … "
                                  + statement[m.end():]).strip(" …"),
                }
        if worst is not None:
            worst["converted_on_partial"] = _converts_on_partial_match(
                worst["statement"], worst["regex"])
            exposure.append(worst)
    return exposure


def _converts_on_partial_match(statement: str, compiled: "re.Pattern") -> bool:
    """
    True if the bridge still writes fields from a pattern that matches in part.

    Asks the real code rather than re-implementing its rule, so this keeps
    working if the matching moves. `fallback=False` isolates the pattern: with
    the standard parser switched off, anything returned came from the pattern
    alone.
    """
    from pattern_bridge import build_parse_result, roles_from_regex   # noqa: PLC0415

    result = build_parse_result(statement, compiled,
                                roles_from_regex(compiled.pattern),
                                split=False, fallback=False)
    return result is not None and bool(result.ranges)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

DEFECTS = {
    "D1": "discontinuous list truncated at the first comma",
    "D2": "enumeration carried only by the end boundary is dropped",
    "D3": "a designation between enumeration and chronology truncates the parse",
    "D4": "a day inside the date voids that boundary's chronology",
    "D5": "unrecognised chronology wording written into a coded subfield",
    "D6": "captioned at issue level only; no volume anywhere",
    "D7": "no caption at all (accepted limitation)",
    "D8": "leading series designation dropped (warned)",
    "D9": "nested parentheses inside a block body",
    "D10": "run-on block statement declined by the complexity guard (by design)",
    "D11": "unexplained N/M marker before the year (warned, by design)",
    "D12": "detector: one cataloguer-visible shape clustered as many patterns",
    "D13": "detector: free text invisible in the pattern label",
    "D14": "detector/converter disagree on a shape both accept",
    "D15": "compressed range collapsed when both endpoints are equal",
    "D16": "the end's chronology written as if it were the start's",
    "D17": "workbench: a pattern claims a substring, discarding the rest",
    "D18": "863 second indicator says uncompressed for a compressed field",
}


def report(detail: bool = False, drift_only: bool = False) -> int:
    entries = load_corpus()
    statements = [e.statement for e in entries]
    outcomes = [Outcome(e) for e in entries]

    by_status = collections.Counter(o.status for o in outcomes)
    by_defect = collections.Counter(o.entry.defect for o in outcomes if o.entry.defect)

    # ── Drift: does the recorded tag still describe what happens? ──
    drift = []
    for o in outcomes:
        recorded = o.entry.status
        observed = o.status
        # "known" is a judgement about a behaviour, not a prediction of it.
        if recorded == "known":
            continue
        if recorded == "" and observed != "ok":
            drift.append((o, "untagged, but now " + observed))
        elif recorded and observed == "ok":
            drift.append((o, f"tagged {o.entry.tag}, but now clean"))
        elif recorded and recorded != observed:
            drift.append((o, f"tagged {o.entry.tag}, but now {observed}"))

    if drift_only:
        if not drift:
            print("No drift: every tag in the corpus still describes what the tools do.")
            return 0
        print(f"{len(drift)} statement(s) no longer match their recorded tag:\n")
        for o, why in drift:
            print(f"  line {o.entry.line}: {why}")
            print(f"    {o.entry.statement}")
            print(f"    -> {o.rendered}")
        return 1

    print("=" * 78)
    print("Textual holdings corpus report")
    print("=" * 78)
    print(f"corpus              {CORPUS.relative_to(REPO_ROOT)}")
    print(f"statements          {len(entries)} unique "
          f"({sum(e.occurrences for e in entries)} lines before de-duplication)")
    print(f"sections            {len(set(e.section for e in entries))}")

    # ── Parser / converter ──
    print()
    print("-" * 78)
    print("Parser and converter")
    print("-" * 78)
    clean = by_status["ok"]
    print(f"  converted cleanly            {clean:3d}  "
          f"({clean / len(entries):.0%})")
    print(f"  converted with values lost   {by_status['loss']:3d}   "
          "<- silent: no warning reaches the cataloguer")
    print(f"  converted with a warning     {by_status['warn']:3d}")
    print(f"  produced no fields at all    {by_status['fail']:3d}")

    undeclared = [o for o in outcomes if o.undeclared and o.status != "fail"]
    print(f"\n  853s declaring a caption their own 863 never fills: {len(undeclared)}")

    print("\n  by defect id:")
    for did in sorted(by_defect, key=lambda d: int(d[1:])):
        print(f"    {did:<4} {by_defect[did]:2d}  {DEFECTS.get(did, '?')}")

    if detail:
        for status, heading in (("loss", "Silent losses"),
                                ("fail", "Refusals"),
                                ("warn", "Warned")):
            group = [o for o in outcomes if o.status == status]
            if not group:
                continue
            print(f"\n  {heading} ({len(group)}):")
            for o in group:
                tag = f"[{o.entry.tag}]" if o.entry.tag else "[untagged]"
                print(f"    {tag} {o.entry.statement}")
                print(f"       -> {o.rendered}")
                if o.dropped_numbers or o.dropped_chron:
                    lost = ", ".join(o.dropped_numbers +
                                     [f"chron {c}" for c in o.dropped_chron])
                    print(f"       lost: {lost}")
                if o.end_only:
                    print(f"       end boundary dropped: {', '.join(o.end_only)}")
                if o.uncoded:
                    print(f"       text in a coded subfield: {'; '.join(o.uncoded)}")
                if o.collapsed:
                    print(f"       range collapsed: {'; '.join(o.collapsed)}")
                if o.phantom_start:
                    print(f"       invented start: {'; '.join(o.phantom_start)}")
                for w in o.result.warnings:
                    print(f"       warn: {w}")

    # ── Detector ──
    print()
    print("-" * 78)
    print("Pattern detector")
    print("-" * 78)
    groups = detect_patterns(statements)
    singletons = [g for g in groups if g.count == 1]
    too_complex = [g for g in groups if g.too_complex]
    imperfect = [g for g in groups if not g.too_complex and g.match_rate < 1.0]

    print(f"  clusters                     {len(groups):3d}  "
          f"for {len(statements)} statements")
    print(f"  singleton clusters           {len(singletons):3d}  "
          f"({len(singletons) / len(groups):.0%} of clusters, "
          "each needing its own confirmation)")
    print(f"  declined as too complex      {len(too_complex):3d}")
    print(f"  clusters not matching all their own members  {len(imperfect):3d}")

    # Fragmentation: one cataloguer-visible shape split across many clusters.
    families: dict[str, list] = collections.defaultdict(list)
    for g in groups:
        families[_coarse_signature(g.signature)].append(g)
    split = {k: v for k, v in families.items() if len(v) > 1}
    covered = sum(sum(g.count for g in v) for v in split.values())
    print(f"\n  shapes split across several clusters  {len(split)}")
    print(f"  statements affected                   {covered}"
          f"  ({covered / len(statements):.0%})")
    print(f"  confirmations they cost                "
          f"{sum(len(v) for v in split.values())}, where {len(split)} would do")
    for k, v in sorted(split.items(), key=lambda kv: -sum(g.count for g in kv[1])):
        total = sum(g.count for g in v)
        print(f"    {total:3d} statements -> {len(v)} clusters   "
              f"{v[0].human_label[:56]}")

    # Two clusters, one label: the cataloguer cannot tell them apart on screen.
    labels = collections.Counter(g.human_label for g in groups)
    collisions = {lbl: n for lbl, n in labels.items() if n > 1}
    print(f"\n  distinct clusters sharing an identical label  {sum(collisions.values())}"
          f" across {len(collisions)} label(s)")
    for lbl in collisions:
        print(f"    {lbl!r}")
        for g in groups:
            if g.human_label == lbl:
                print(f"       n={g.count}  e.g. {g.examples[0]}")

    if detail and too_complex:
        print("\n  Declined as too complex:")
        for g in too_complex:
            print(f"    tokens={g.token_count}  {g.examples[0][:100]}...")

    # ── Workbench ──
    print()
    print("-" * 78)
    print("Workbench (confirmed-pattern path)")
    print("-" * 78)
    exposure = pattern_path_exposure(statements, groups)
    leaked = [e for e in exposure if e["converted_on_partial"]]

    print(f"  statements some other cluster's regex matches only in part"
          f"  {len(exposure):3d}  ({len(exposure) / len(statements):.0%})")
    if exposure:
        worst = min(exposure, key=lambda e: e["fraction"])
        print(f"  smallest such span                                       "
              f"  {worst['fraction']:.0%} of its statement")
    print(f"  of those, ones a pattern would still convert on"
          f"            {len(leaked):3d}")

    if leaked:
        print("\n  REGRESSION: build_parse_result() is converting on a partial")
        print("  match again. Everything outside the matched span is being")
        print("  written off. See D17 in CORPUS-FINDINGS.md.")
        for e in leaked[:10]:
            print(f"    {e['statement']}")
            print(f"      kept     : {e['matched']}")
            print(f"      discarded: {e['discarded']}")
    else:
        print("\n  None convert: build_parse_result() requires the pattern to span")
        print("  the whole segment, so a partial match is treated as no match and")
        print("  the statement goes to the standard parser whole. The count above")
        print("  is how often that rule is what stands between a confirmed")
        print("  pattern and a truncated record -- it is the fix doing work, not")
        print("  a problem.")

    if detail:
        for e in sorted(exposure, key=lambda e: e["fraction"])[:10]:
            print(f"\n    {e['fraction']:.0%} would have been consumed by a cluster of "
                  f"{e['cluster_size']}  ({e['label']})")
            print(f"      statement: {e['statement']}")
            print(f"      that span: {e['matched']}")
            print(f"      refused, so this survives: {e['discarded']}")

    # ── Drift ──
    print()
    print("-" * 78)
    print("Tag drift")
    print("-" * 78)
    if not drift:
        print("  none - every tag still describes what the tools do")
    else:
        print(f"  {len(drift)} statement(s) no longer match their recorded tag:")
        for o, why in drift:
            print(f"    line {o.entry.line}: {why}")
            print(f"      {o.entry.statement}")
    print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--detail", action="store_true",
                    help="list every affected statement, not just the counts")
    ap.add_argument("--drift", action="store_true",
                    help="print only the tags that no longer hold; exit 1 if any do not")
    args = ap.parse_args()
    return report(detail=args.detail, drift_only=args.drift)


if __name__ == "__main__":
    raise SystemExit(main())
