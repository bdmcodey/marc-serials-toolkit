# Corpus findings — what `textual_holdings_corpus.txt` reveals

Written 1 September 2026, after adopting the hand-collected 866 `$a` corpus that
predates the toolkit — the examples the original monolithic regex was written
against — as `data/textual_holdings_corpus.txt`.

**Nothing in the parser, the converter or the detector was changed.** This is a
log of what the corpus exposes, so that fixing any of it is a deliberate,
separately reviewable decision.

Reproduce every number below with:

```bash
python scripts/corpus_report.py            # the summary
python scripts/corpus_report.py --detail   # every affected statement
python scripts/corpus_report.py --drift    # only the tags that no longer hold
```

## Headline

| | |
|---|---|
| statements | 110 unique (125 before de-duplication), 9 sections |
| converted cleanly | **78 (71%)** |
| converted with values silently dropped | **23 (21%)**, 22 of them defects |
| converted with a warning | 3 |
| produced no fields at all | 6 (5%) |
| detector clusters | 54 for 110 statements, 38 of them singletons |

The number that matters is not the 6 refusals. It is the **23 silent losses**.

A refusal is safe: the Converter writes nothing, the 866 survives, and the
statement lands in the cataloguer's review queue. A silent loss is not.
`converter/app.py` defaults `remove_866` to `True`, so once *anything* has been
written from a statement the source text is deleted from the record — and for
these 23 the generated 853/863 does not carry everything the 866 said. The
holdings are gone, and nothing on screen says so.

The Workbench is safer here: since "Keep the original 866s by default" it
defaults `remove_866` to `False`. The Converter still does not.

Under the old monolithic regex most of these statements parsed. The three most
damaging classes below (D1, D2, D3) are regressions in coverage relative to it,
not shapes that were always out of reach.

## Converter and parser

### D1 — a discontinuous list is truncated at its first comma · 7 statements · **worst**

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

### D2 — enumeration stated only at the end of a range never reaches the 863 · 8 statements

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

### D12 — one shape is clustered as many patterns · 44 statements (40%)

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

## What I would do next, in order

1. **D1 and D3 together** — move the partial-match guard in `_parse_unit()` out of
   the captionless branch so it applies whenever the match does not account for
   the whole unit. That alone converts 10 silent losses into visible refusals,
   which is a strict improvement even before either shape is properly parsed.
   Handling the comma list properly is the larger, separate job.
2. **D2** — give enumeration the same end-boundary fallback chronology already
   has. Small, local, 8 statements.
3. **D6** — let the enumeration block match an issue-first unit. No guessing
   required; three statements here and a common real shape.
4. **D5** — refuse to write text into a subfield declared as coded; hold for
   review instead.
5. **D12/D13** — merge `MON`/`SEASON`, give `/` a token kind, and show UNKNOWN
   runs in the label.

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
