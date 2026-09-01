# Corpus findings — what `textual_holdings_corpus.txt` reveals

Written 1 September 2026, after adopting the hand-collected 866 `$a` corpus that
predates the toolkit — the examples the original monolithic regex was written
against — as `data/textual_holdings_corpus.txt`.

This is a log of what the corpus exposes, so that fixing any of it is a
deliberate, separately reviewable decision. Everything here was found before
anything was changed.

**D17 and D18 are fixed** as of 0.6.1, and **D2, D15 and D16** as of 0.6.2
(1 September 2026). Their sections below are kept and marked, because the
reasoning is the record of why the code looks the way it does now. Everything
else is open.

Reproduce every number below with:

```bash
python scripts/corpus_report.py            # the summary
python scripts/corpus_report.py --detail   # every affected statement
python scripts/corpus_report.py --drift    # only the tags that no longer hold
```

## Headline

| | at 0.6.0 | now (0.6.2) |
|---|---|---|
| statements | 112 unique (127 before de-duplication), 10 sections | — |
| converted cleanly | 67 (60%) | **79 (71%)** |
| converted with values **silently** dropped | 36 (32%) | **15 (13%)** |
| converted, and told the cataloguer what it dropped | 3 | **12** |
| produced no fields at all | 6 (5%) | 6 (5%) |
| detector clusters | 55, 39 of them singletons | unchanged |
| statements a pattern could claim only part of | 37 (33%) | 0 convert on one |

The silent-loss column is the one to watch. Half of the drop is values now
*encoded* (D15); the rest are values still not encoded but now **named in a
warning**, which moves them from lost to accounted for.

The number that matters is not the 6 refusals. It was the **36 silent losses**;
15 remain.

A refusal is safe: the Converter writes nothing, the 866 survives, and the
statement lands in the cataloguer's review queue. A silent loss is not.
`converter/app.py` defaults `remove_866` to `True`, so once *anything* has been
written from a statement the source text is deleted from the record — and for
these the generated 853/863 does not carry everything the 866 said. The holdings
are gone, and nothing on screen says so.

The Workbench is safer here: since "Keep the original 866s by default" it
defaults `remove_866` to `False`. The Converter still does not.

Under the old monolithic regex most of these statements parsed. The three most
damaging classes below (D1, D2, D3) are regressions in coverage relative to it,
not shapes that were always out of reach.

**Revised 1 September 2026** after reviewing a real `.mrc` through the Workbench.
D15–D18 below came out of that review, and two of them (D15, D17) are far more
common than the single records that exposed them. The clean rate fell from 71%
to 60% purely because the audit got sharper — no code changed.

## Converter and parser

### D1 — a discontinuous list is truncated at its first comma · 7 statements · **worst per statement**

```
v. 19 nos. 1, 3, 5, 7-12 (Jan, Mar, May, Jul-Dec 1915)
  -> 853 $a v. $b no.
     863 $a 19 $b 1
```

Issues 3, 5 and 7–12 are gone. So are all five months. So is the year — the 853
does not even declare `$i`, because no chronology was ever parsed. Eleven of the
statement's twelve assertions are discarded, silently, and the 866 is then
removed.

Five of the seven live in the `dentistry` section, which is one title's actual
run — five of its seven statements. A library holding that title would lose
most of it in a single pass.

**Cause.** `_split_ranges()` declines to split at these commas — correctly, since
`3, 5` is not a new range — so the whole statement reaches `_parse_unit()` as one
unit. `_ENUM_CHRON_RE` matches `v. 19 nos. 1` and stops at the comma. The guard
in `_parse_unit()` that exists precisely to refuse a partial match —

```python
if not m.group("iss_cap") or m.end() != len(text):
    return None
```

— is inside `if m.group("vol_num") and not m.group("vol_cap")`. It only runs for
a *captionless* number. Here `v.` is present, so the prefix match is accepted and
the remaining 34 characters are dropped without comment.

That the guard's own comment describes exactly this failure ("the 866 is removed
once anything is written from it, so the second issue and both seasons would go
with it") suggests the case was understood and the guard simply landed one
condition too deep.

### D2 — enumeration stated only at the end of a range never reaches the 863 · 8 statements · **FIXED in 0.6.2**

```
v. 1 - v. 55 no. 3 (1927-1982)
  -> 853 $a v. $b no. $i (year)
     863 $a 1-55 $i 1927-1982          <- no $b at all
```

The 853 declares a `no.` caption and the 863 under it carries no issue. The
record now says "this serial is numbered by issue" and gives no issue anywhere —
worse than either stating it or omitting the caption.

**Cause.** `_build_863_for_range()` gives chronology an end-boundary fallback:

```python
start_year = s.year if s.year is not None else (e.year if e else None)
```

Enumeration gets none. `_enum_value(None, "3", False)` returns `None` on its
first line, and the subfield is never written. The asymmetry looks accidental —
the chronology fallback has a comment explaining why it is needed
("chron at end only"), and the identical argument applies to enumeration.

Affected: `v. 1 (1973)-v. 11 no. 9 (Sep 1983)`, `v. 1 (1956) - v. 51 nos. 1-2
(2006)`, `v. 78 - v. 93 no. 3 (1981 - Sep 1996)`, `v. 1 - v. 55 no. 3
(1927-1982)`, `v. 1-v. 17 no. 3 (1981-October 1997)`, `v. 18-v. 19 no. 2
(1998-Summer 1999)`, `v. 1 (1956)-v.51 nos.1-2 (2006)`, `v. 1-v. 2 no. 2
(1984-Mar/Apr 1985)`.

Note the last one: its issue is `2` and its volume range renders as `1-2`, so a
digit-counting audit sees a "2" in the output and reports nothing. Only checking
whether the subfield was written at all finds it — `scripts/corpus_report.py`
does both, and this is why.

**Fixed in 0.6.2, but not the way this section proposed.** Giving enumeration
the same end-boundary fallback chronology had would have been wrong: working
through D16 showed that fallback is itself the bug in the other direction. The
value is still not written — there is no MARC notation for half a range, and
inventing a start would be worse than omitting the level — but the conversion
now **names the issue it could not place**, so it is accounted for instead of
vanishing. See "One rule for both ends" below.

### D3 — a designation between enumeration and chronology truncates the parse · 3 statements

```
v. 58 Suppl. (Sep 2003)   ->  853 $a v.
                              863 $a 58
```

`Suppl.` is not a caption the enumeration block recognises, so the match ends
after `v. 58 `, the chronology block never sees its `(`, and September 2003
disappears. Same mechanism as D1 — an accepted prefix match with a caption
present — and the same fix would cover both.

This is the entire `supplements` and `compendium-suppl` shape. Supplements are
common, and a supplement's chronology is often the only thing distinguishing it
from the main run.

### D4 — a day inside the date voids that boundary's chronology · 1 statement

```
v. 34 no. 8/9-v. 35 no. 23/24 (Apr 18, 1996-Dec 1997)
  -> 863 $a 34-35 $b 8/9-23/24 $i 1997 $j 12
```

`Apr 18, 1996` matches none of `_parse_chron_single()`'s four alternatives — each
requires the year adjacent to the month — so it returns `(None, None)` and the
start year and month are both lost. `$i 1997` alone now claims the run begins in
1997.

Only one statement here, but day-level dates are ordinary in weeklies and
newspapers; the `block-grammar` section is full of them, and there they are
dropped *by design* (`_bracket_chron_unit` documents "trailing day dropped").
The inconsistency is that the block grammar drops the day and keeps the month,
while this path drops the whole boundary.

### D5 — chronology wording that is not a code is written into a coded subfield · 3 statements

```
v. 15 (1998 Buyers Guide)              -> 863 $i 1998 $j Buyers Guide
v. 15 no. 6 - v. 23 nos. 2/3 (...)     -> 863 $j 11/12-Late Summer
2018: ([Sum])                          -> 863 $j Sum
```

The 853 labels `$j` as `(month)` or `(season)`; an 863 under it should hold
`01`–`12` or `21`–`24`. `_chron_unit_value()` falls back to
`normalise_chron_unit()`, which returns unrecognised text unchanged, and the
value goes through. `$j 11/12-Late Summer` mixes codes and prose in one subfield.

The fallback is the right instinct — dropping the text would be worse — but the
value needs to go somewhere that admits text, or the field needs to be held for
review, rather than into a subfield the record declares as coded.

`Buyers Guide` is a different problem wearing the same clothes: it is not
chronology at all. It is a named issue that happens to sit in the parenthesis
where chronology normally lives.

### D6 — statements captioned at issue level only are refused outright · 3 statements

```
no. 26 (May 1994)-no. 37 (May 2000)    ->  nothing
no. 41 (May 2002)                      ->  nothing
```

`_ENUM_CHRON_RE`'s enumeration block is `(vol_cap)? vol_num (iss_cap iss_num)?`.
The issue group can only follow a number, so an issue-first unit has no path
through the grammar at all and the statement fails whole.

**This is not the "captionless" family, and I would not file it with them.** The
caption is right there — `no.` — and it is unambiguous. A serial numbered
continuously by issue with no volume level is a completely ordinary thing
(monographic series, numbered reports, many newsletters). Of the six refusals in
this corpus, these three are the ones worth fixing; nothing has to be guessed.

### D7 — genuinely captionless statements · 3 statements · expected fail

```
8,13,15,17,19,20-(1982-1994)
50th Anniversary Issue (2017)
Special Issue (October/November 1995)
```

These the monolith never handled either, and they should stay failing. Nothing in
`8,13,15,...` says whether those are volumes, issues or years, and refusing is
the documented, correct behaviour — the same argument `HANDOFF.md` makes about
`?: 16`. A cataloguer supplies the level; the parser cannot.

`Special Issue` and `50th Anniversary Issue` are worth one note: the Workbench's
confirm step is exactly the mechanism that could convert these, since a human
says once what the captured values mean.

### D8, D9, D11 — smaller things, all warned or by design

- **D8** `Series 1, v. 6 no. 1 (Summer/Fall 1992)` — the series designation is
  dropped, but with a warning naming it. Correct handling of something it cannot
  encode; recorded so it is not mistaken for a silent loss.
- **D9** `N1984: (2 (1))M1985: 2 (2 [summer])` — `_BLOCK_RE`'s body allows one
  nesting level, and the inner `(1)` is lost. The statement does warn, about the
  `N`/`M` markers rather than about this.
- **D11** `N?: 1 (4)1984: ...` — unexplained markers, warned by design.
- **D10** the long `ONE` run-on — the detector's complexity guard declines it and
  the parser drops the day from each bracketed date. Both documented, both
  intended.

## Pattern detector

The detector's *correctness* holds up well: every cluster that generates a regex
matches 100% of its own members, and only the one run-on trips
`MAX_PATTERN_TOKENS`. The problems are all about how much work it hands the
cataloguer, and one is about what it shows them.

### D12 — one shape is clustered as many patterns · 45 statements (40%)

54 clusters for 110 statements, 38 of them singletons. Four cataloguer-visible
shapes account for the worst of it:

| statements | clusters | shape |
|---|---|---|
| 23 | **7** | `VOLISS — VOLISS(chron YEAR-chron YEAR)` |
| 10 | 2 | `VOLISS#-ISS(chron-chron YEAR)` |
| 7 | 3 | `VOLISS(chron YEAR) — VOLISS(chron YEAR)` |
| 4 | 3 | `VOL — VOLISS(YEAR-chron YEAR)` |

Those 44 statements cost **15 confirmations where 4 would do**.

Two causes, both in the signature:

1. **`MON` and `SEASON` are distinct kinds.** `(Sep 1944 - Aug 1945)` and
   `(Winter 1986 - Summer 1987)` are the same shape to a cataloguer and different
   signatures to the detector. They occupy the same slot and already share a
   capture-group name (`boundary_name("month")` is used for both).
2. **The slash has no token kind.** `Jul/Aug` tokenises as `MON | UNKNOWN | MON`,
   so `(Jul/Aug 2017)` and `(Apr 2019)` land in different clusters. 17 of the 110
   statements contain a slash, and it is never noise: it means a combined issue
   (`no. 1/2`), a combined month (`Jul/Aug`) or a split year (`1990/91`). Falling
   to `UNKNOWN` also means generated regexes carry `.{1,8}?` where a literal `/`
   belongs, matching arbitrary text in a position with a definite meaning.

Merging `MON`/`SEASON` alone takes 54 clusters to 50; adding a slash token takes
it to 43. Neither is a full fix — much of the remaining fragmentation is real
structural variety, which is the corpus being honest — but the two together
remove the fragmentation a cataloguer would call spurious.

### D13 — free text is invisible in the pattern label

`_compact_label()` has a branch for every token kind except `UNKNOWN`, which it
silently omits. So:

```
v. 6 (1935)                  ->  label "VOL(YEAR)"
v. 15 (1998 Buyers Guide)    ->  label "VOL(YEAR)"
```

Two distinct clusters, one identical label. On screen they are two rows a
cataloguer cannot tell apart, and confirming either says nothing about the other.
The same silence hides `Suppl.` in `v. 58 Suppl. (Sep 2003)` (labelled
`VOL(MONYEAR)`, indistinguishable from a clean statement) and `Anniversary` in
`50th Anniversary Issue (2017)`.

This is the label a pattern is chosen by. A `…` or `‹text›` marker where an
UNKNOWN run sits would be enough.

### D14 — detector and converter disagree, and the Workbench sits between them

`8,13,15,17,19,20-(1982-1994)` clusters happily as `#,#,#,#,#,#-(YEAR-YEAR)`,
generates a valid regex, and matches itself 100%. The converter refuses it
outright (D7, correctly).

The detector is answering "what shape is this", the converter "what does it
mean", and those genuinely differ — but the Workbench presents a confirmed
pattern as a thing that converts. A cataloguer who confirms this pattern has
supplied the missing level, so this may be the intended path working as designed;
worth deciding explicitly rather than leaving to inference.

Two smaller ones:

- `50th` tokenises as `NUMBER "50t"` + `UNKNOWN "h"`, because `NUMBER` is
  `\d+[a-zA-Z]?`. The capture group holds `50t`.
- `split_multi_range()` splits `Series 1, v. 6 no. 1 (...)` into `Series 1` and
  the rest, so `Series 1` is clustered as though it were a holdings range.

## From reviewing a real `.mrc` through the Workbench

Two records raised these; the corpus then showed how common they are. Both
premises checked out against MARC 21 — see "Checking against the standard".

### D15 — a compressed range collapses when both endpoints are equal · 12 statements · **FIXED in 0.6.2**

```
v. 41 no. 1-v. 43 no. 1 (Jun 1984-Jan/Apr 1986)
  generated: 863 41 $8 1.1 $a 41-43 $b 1   $i 1984-1986 $j 06-01/04
  correct  : 863 41 $8 1.1 $a 41-43 $b 1-1 $i 1984-1986 $j 06-01/04
```

A compressed 863 states the first part held and the last part held, so every
level has to appear at both ends. `_enum_value()` ends with

```python
return f"{start}-{end}" if end != start else start
```

so equal endpoints collapse to one value. `$a 41-43 $b 1` cannot be read back as
*v.41:no.1 – v.43:no.1*: it equally describes issue 1 of each of volumes 41
through 43. The endpoint pairing is destroyed.

Collapsing is *not* always wrong, and the audit only flags it when a more
significant level actually ranges. `v. 43 no. 6 - v. 43 no. 7` is fully
recoverable from `$a 43 $b 6-7`, because the volume does not range — so it is
not flagged. Twelve statements are, including `$j 01` under `$i 2014-2022`,
where the same ambiguity hits chronology.

**Fixed in 0.6.2** in two places, because the collapse happens twice. The
obvious one is `_enum_value()`. The second only showed up while testing: when a
single chronology group spans the range — `(Jan 1956 - Jan 1957)` — the parser
folds it onto the end boundary, so `_parse_chron()` is the *only* code that ever
sees both months, and it was collapsing them there. Four of the twelve were
being half-fixed until that was found.

That second site also shows why the rule cannot be applied later: by the time
`_build_863_for_range()` sees a lone `09`, "Sep 1956 - Sep 1957" and
"1981 - Sep 1996" look identical, and repeating the second would invent a month
the statement never gave. `_parse_chron()` can still tell them apart, so that is
where it belongs.

A value that is already a range is left alone — `no. 3-4 - no. 3-4` would
otherwise become the unreadable `3-4-3-4`.

### D16 — the end's chronology written as if it were the start's · 1 statement · **FIXED in 0.6.2**

```
v. 1 no. 1 (1995)-v. 12 no. 4 (December 2006)
  generated: 863 41 $8 1.1 $a 1-12 $b 1-4 $i 1995-2006 $j 12
  correct  : 863 41 $8 1.1 $a 1-12 $b 1-4 $i 1995-2006
```

`_build_863_for_range()` falls back to the end boundary when the start has no
value:

```python
start_month = s.month if s.month is not None else (e.month if e else None)
```

That is right for `v.1:no.1-v.2:no.4(1990-1991)`, where one chronology group
covers the whole range. It is wrong here, where *both* boundaries have their own
parenthesis and only the second names a month: the field now asserts the run
begins in December. The start month is genuinely unknown and unknowable from the
statement, so dropping `$j` is the honest output — the audit distinguishes the
two shapes by whether the other chronology level proves both boundaries carried
a group of their own.

This is the exact mirror of D2. There the end-only fallback is *missing* for
enumeration; here it is *too eager* for chronology. Both come from the same
asymmetry and were fixed together.

**Fixed in 0.6.2.** `$j` is omitted and a warning names the December it could
not place.

### D17 — a confirmed pattern claims a substring and discards the rest · 37 statements (33%) · **FIXED in 0.6.1**

This is the one that produced the reported output, and it is Workbench-only —
`parse_866()` reads the statement correctly.

```
v. 1 no. 1 (1995)-v. 12 no. 4 (December 2006)
  via parse_866():        863 $a 1-12 $b 1-4 $i 1995-2006 $j 12
  via a confirmed pattern: 863 $a 12   $b 4   $i 2006      $j 12
```

Reproduced exactly, **with no warning**, by confirming the corpus's single
largest cluster — `VOLISS(MONYEAR)`, the plain `v. 9 no. 1 (Nov 1902)` shape —
and running this statement through it.

**Cause.** `pattern_bridge.build_parse_result()`:

```python
m = compiled.fullmatch(seg) or compiled.search(seg)
```

`fullmatch` fails, `search` succeeds on the tail, and the pattern claims
`v. 12 no. 4 (December 2006)` — characters 18 to 45. The first half is never
looked at again. `apply_patterns()` returns the first pattern that matches, so
the shortest, commonest pattern — the one a cataloguer confirms first, because
it has the biggest count — beats the longer correct one.

Across the corpus **37 of 112 statements (33%)** can be claimed in part by some
other cluster's regex, the worst keeping 32% of its statement.

The same `fullmatch() or search()` idiom is in `pattern_detector._validate()`,
where it inflates the reported match rate: a cluster can report 100% while its
regex only spans part of some members. There it is documented as deliberate, so
partly-parsed multi-range strings still count as a hit. That rationale does not
carry over to the bridge, where the match decides what gets written.

Worth noting against the file's own reasoning: `build_parse_result()` already
argues, at length and correctly, that half a statement is worse than none —
"the 866 is removed once anything is written from it… All or nothing keeps the
field intact." That guarantee was enforced *between* segments and not *within*
one.

**Fixed in 0.6.1.** `build_parse_result()` now requires `fullmatch`; a partial
match means the pattern does not describe the statement, so it is treated as no
match and the statement goes to the standard parser whole. Three places that
*told* the cataloguer a partial match was a match changed with it, because the
confirmation screen is where a wrong pattern gets confirmed in the first place:
`_validate()` in the detector (the cluster match rate), and `_example_values()`
and `/api/test-regex` in the Workbench. The detector's own Test button already
distinguished full from partial and was left alone.

Checked before changing it: **0 of 111** statements fail to fullmatch their own
cluster's regex, so the `search` fallback never helped a pattern match its own
members — it only ever let one claim a foreign statement.
`scripts/corpus_report.py` now re-asks the real bridge whether any of the 37
would still convert on a partial match, and says REGRESSION if one does.

### D18 — the 863 second indicator contradicts the field · **FIXED in 0.6.1**

`_build_863_for_range()` writes:

```python
indicator1="4",  # 4 = no information provided / n/a
indicator2="1",  # 1 = compressed using / range designation
```

Both comments misstate the standard, and one of the values is wrong.

Second indicator in 863 is **Form of holdings**: `0` Compressed, `1`
Uncompressed, `2` Compressed use textual string, `3` Uncompressed use textual
string. The tool writes `1` — uncompressed, meaning each part itemised
separately — on fields like `$a 41-43 $i 1984-1986`, which are compressed ranges.
Every generated 863 says the opposite of what it contains. It should be `0`.

First indicator is **Field encoding level**, values 3/4/5 matching Leader/17, not
"no information provided". `4` is a defensible value for enum-and-chron holdings,
so the output is right and only the comment is wrong — but it should agree with
whatever the record's Leader/17 says.

**Fixed in 0.6.1**, second indicator only: `0`. The first indicator keeps `4`
and gains a comment that states the rule; reconciling it with Leader/17 is a
separate change, since nothing currently reads the Leader. A test pins the pair,
because a single indicator character has no visible effect on screen and nothing
else would notice it drifting back.

The `853 31` the tool writes is fine: first indicator `3` (compressibility
unknown) and second `1` (captions verified, all levels may not be present) are
both reasonable defaults. Note that `853` first indicator `3` and an `863`
claiming to be uncompressed are at least consistent in being uninformative —
setting the 863 indicator to `0` without revisiting the 853's would be a partial
fix.

## One rule for both ends

D2, D15 and D16 were three symptoms of one thing: `_build_863_for_range()` had
no single answer to "what does a level's pair of endpoints become". Chronology
had an end-boundary fallback and enumeration had none; equal endpoints collapsed
in both. 0.6.2 replaces all of it with `_hierarchy_values()`, which walks one
hierarchy at a time — enumeration `vol → issue → part`, chronology
`year → month`, independent of each other — and answers four cases:

| what the range states | what is written |
|---|---|
| both ends, different | `41-43` |
| both ends, equal, something above it ranges | `1-1` — the pairing is the point |
| both ends, equal, nothing above it ranges | `43` — nothing to pair with |
| the start only | the start's value |
| the end only, start says nothing at all in this hierarchy | the end's value — one group covering the whole range |
| the end only, start states other levels | **omitted, and named in a warning** |

The last row is the one that took the thinking. Both boundaries were written
out and only one names this level, so there is no range to express and no
notation for half of one. Writing it asserts something false about the other
end; dropping it silently loses a value the cataloguer gave. Naming it does
neither:

```
v. 1 - v. 55 no. 3 (1927-1982)
  863 40 $8 1.1 $a 1-55 $i 1927-1982
  "Only the end of this range gives an issue (3); a compressed 863 records the
   first and last part held, so with no issue at the start it was left out."
```

This is the third bucket from the bounded-error proposal, working. The value is
neither encoded nor lost — it is **accounted for**, and the cataloguer can act
on a specific claim rather than wondering what else went missing.

The corpus report follows the same distinction: a value a warning names no
longer counts as a silent loss, which is why the clean rate moved from 60% to
71% while six statements moved from "lost" to "warned". If that warning were
ever removed, the report would count them as losses again.

### What this deliberately did not change

Two shapes were left alone, and both are worth knowing about:

- **The start-only mirror.** `v. 118 no. 1 (Spring 2012)-v. 122 no. 1 (2016)`
  writes `$i 2012-2016 $j 21`, where the start names a season and the end does
  not. That is the same ambiguity as the fixed cases, pointing the other way,
  and dropping it would lose a value the statement does give. Left as it is; not
  yet decided.
- **`(1981 - Sep 1996)`** still writes `$j 09`. Only one side of the group named
  a month, so `09-09` would invent one, and there is no way to say "the end
  only". Same category as the row above.

## Checking against the standard

Both premises hold.

**`$b 1-1`.** MARC 21 defines compressed form as "a summarized form containing
the enumeration and chronology of more than one part expressed as a range of
holdings", i.e. the first part held and the last part held. Implementation
guidance is explicit that *all levels of enumeration must be repeated at the
beginning and end of each range held* — the canonical example being
`v.1:no.1(1976:winter)-v.2:no.3(1976:summer)`, where the year is repeated even
though both ends are 1976. So `$a 41-43 $b 1-1` is correct and `$b 1` is not.

**Dropping `$j`.** Nothing in the format lets a chronology subfield say "the end
only". The subfield either carries the range or it does not, so writing `$j 12`
for a range beginning in 1995 states something false about the first part held.
Omitting it is the correct reading, and matches how `853` second indicator `1`
already declares that not every level may be present.

One caveat on sourcing: `loc.gov`, OCLC and itsmarc are all blocked by this
environment's network policy, so I could not fetch the primary pages directly and
worked from search results quoting them, plus the CONSER/Yale implementation
guidance. The indicator value lists and the compression rule were consistent
across every source that surfaced, but the `$b 1-1` conclusion rests on
implementation guidance rather than a verbatim MARC 21 example, and is worth one
confirmation against the LoC page before it goes in a slide.

## Catching silent drops: a proposal

The two records above are a good argument for a *bounded-error* guarantee,
because neither is a case where the tool could have got the answer right. In
D16 the start month is not in the statement. In D17 the pattern genuinely does
not describe the statement. What went wrong is not that the tool was unsure —
it is that the output did not say so. The error was unbounded: nothing in the
record, the screen, or the log distinguished a conversion that used everything
from one that used a third of it.

The tool cannot promise its readings are right. It can promise something
narrower and checkable: **no value leaves the 866 unaccounted for.** Every
token in the source ends in exactly one of three buckets, and the third is never
empty-by-default:

1. **encoded** — it reached a subfield;
2. **deliberately dropped** — with a reason on the record ("day-of-month not
   encoded", "role set to Not encoded");
3. **unaccounted** — nobody claimed it. This is the bucket that must force the
   statement into review.

That turns an unbounded failure ("something may be missing, somewhere") into a
bounded one ("these three tokens are missing, here they are"). It is also
exactly what a cataloguer can act on.

Three checks implement it, all cheap, and all now prototyped in
`scripts/corpus_report.py` where they find both reported cases automatically:

**1. Span coverage — the pattern path.** A match must consume the whole segment.
This is nearly free: drop the `or compiled.search(seg)` fallback, or keep it and
compare `m.end() - m.start()` against `len(seg)`, reporting the unconsumed text
verbatim. It catches D17 outright and would have caught it the first time
anyone confirmed a short pattern. `pattern_path_exposure()` in the report script
is this check.

**2. Value conservation — both paths.** Extract the numbers and month/season
words from the source, extract them from the generated fields, and diff. The
report script's audit does this and is deliberately conservative (a dropped
value that coincides with another value already in the output is not counted),
so it under-reports and never cries wolf. Everything it does report is real.

**3. Structural invariants — the converter.** Cheaper than conservation and
sharper, because each one names a specific defect rather than a missing digit:

   - every caption the 853 declares has a value in its 863 (finds D2);
   - a level whose endpoints are equal under a ranging level is written
     `x-x`, not `x` (finds D15);
   - a chronology subfield holds codes, not prose (finds D5);
   - a value that belongs to one boundary is not written as the other's
     (finds D16).

Where this should live matters more than the checks themselves. Today the
Converter's contract is "produce fields, remove the 866". The bounded version is
**"produce fields, account for every token, and only remove the 866 when the
third bucket is empty"** — the 866 stays whenever anything is unaccounted for,
and the statement is marked for review with the unaccounted tokens listed. That
inverts the current default in the safe direction: silence stops meaning success.

Two things follow that are worth saying in the talk. First, the receipt is
useful even when nothing is wrong — a cataloguer reviewing 500 records wants to
see *which* statements were fully accounted for, so attention goes to the rest.
Second, none of this requires better parsing. It is orthogonal: the parser can
stay exactly as wrong as it is today and the failure still becomes bounded,
visible and countable. That is the whole point — the guarantee is about
detection, not accuracy, and it is achievable now in a way that "parse
everything correctly" is not.

## What I would do next, in order

0. ~~**D17 first, before anything else.**~~ Done in 0.6.1.
1. ~~**D18** — set the 863 second indicator to `0`.~~ Done in 0.6.1.
2. ~~**D15 and D16 together**~~ Done in 0.6.2, with D2 — one rule for how a
   level's two endpoints become a subfield value. See "One rule for both ends".
3. **D1 and D3 together** — move the partial-match guard in `_parse_unit()` out of
   the captionless branch so it applies whenever the match does not account for
   the whole unit. That alone converts 10 silent losses into visible refusals,
   which is a strict improvement even before either shape is properly parsed.
   Handling the comma list properly is the larger, separate job.
4. ~~**D2**~~ Done in 0.6.2. The fix was not the fallback proposed above —
   see D2's section for why that would have been wrong.
5. **D6** — let the enumeration block match an issue-first unit. No guessing
   required; three statements here and a common real shape.
6. **D5** — refuse to write text into a subfield declared as coded; hold for
   review instead.
7. **D12/D13** — merge `MON`/`SEASON`, give `/` a token kind, and show UNKNOWN
   runs in the label.

The bounded-error work above cuts across all of these and is worth doing
alongside rather than after: every fix on this list is easier to trust when the
report can show what it changed.

## Requested, not yet started

Raised 1 September 2026 alongside D15–D18, recorded here so they are not lost.
None of these is started; the first is a modelling change and the rest are
Workbench UI.

- **Enumeration levels are positional, not named.** `caption_slot()` maps `no.`
  to `issue` and `_build_853()` puts issue in `$b`, so the level a caption
  occupies is hard-coded to the word used. It should not be: an issue can be the
  *top* level of enumeration and belong in `$a`, and a title can carry three
  levels (volume, issue, part) needing `$a $b $c`. The word "issue", "number" or
  "part" carries no inherent level. This is the same root as D6 — which is not
  really "issue-first statements are refused" but "the model cannot express an
  enumeration hierarchy that is not volume-then-issue" — so the two should be
  designed together rather than patched separately.
- **Skip a pattern or a record.** A button that removes a pattern or a record
  from conversion entirely, leaving it untouched.
- **Split on top-level commas, semicolons or slashes should default to OFF.**
- **The pattern library needs to collapse.** It is unusable at length with a
  large `.mrc` loaded; scrolling past it to reach the next step is the whole
  interaction.
- **Jump from a record in Convert back to its pattern**, unhiding the library if
  collapsed, so a mistake spotted during review can be fixed at its source. Any
  record already reviewed that the edited pattern touches has to go back to
  unreviewed.

## A note on this corpus

There are no tests here. `scripts/corpus_report.py` reports and asserts nothing,
deliberately: adding xfail tests would have changed the suite's output in the
same commit that introduced the corpus. Turning D1–D6 into `xfail` cases in
`tests/test_holdings_parser.py`, each one going green as it is fixed, is the
natural next commit and matches how the suite already documents known defects.

The corpus covers statement-level behaviour only. Record-level concerns — `$8`
linking across statements, conforming to an existing 853, multiple 866s on one
record — are not touched by it, and `data/messy_holdings.mrc` remains the fixture
for those.

Provenance is worth stating plainly: unlike the two `.mrc` files in `data/`,
which are invented, this corpus is transcribed from real catalogue records. It
holds no patron data, no local identifiers and no institutional codes — only
enumeration and chronology strings, plus publicly known serial titles as section
headings. `.gitignore`'s `*.mrc` rule keeps real holdings *files* out of the
repository and is untouched by adding this `.txt`. If that trade is not wanted,
the corpus can be moved behind `MARC_TEST_DATA_DIR` the way the private `.mrc`
files already are.
