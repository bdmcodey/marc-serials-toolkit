# Corpus findings — what `textual_holdings_corpus.txt` reveals

Written 1 September 2026, after adopting the hand-collected 866 `$a` corpus that
predates the toolkit — the examples the original monolithic regex was written
against — as `data/textual_holdings_corpus.txt`.

This is a log of what the corpus exposes, so that fixing any of it is a
deliberate, separately reviewable decision. Everything here was found before
anything was changed.

> **A note on file names.** The sections below name modules as they were called
> when each finding was written. The engines have since moved into one package
> and been renamed, and the entries are left as they stand rather than edited,
> because the reasoning is the record. The mapping is:
>
> | Then | Now |
> |---|---|
> | `converter/holdings_parser.py` | `marc_serials/parser.py` |
> | `converter/marc_converter.py` | `marc_serials/converter.py` |
> | `pattern-detector/pattern_detector.py` | `marc_serials/detector.py` |
> | `pattern-detector/regex_budget.py` | `marc_serials/budget.py` |
> | `workbench/pattern_bridge.py` | `marc_serials/bridge.py` |
> | `workbench/pattern_library.py` | `marc_serials/library.py` |
>
> Their test modules moved with them: `tests/test_holdings_parser.py` is now
> `tests/test_parser.py`, and so on.
>
> The three Flask applications were merged into one on 16 September 2026.
> `workbench/app.py` is now `marc_serials/webapp.py`; `converter/app.py` and
> `pattern-detector/app.py` are gone, their routes carried across. Where an
> entry below says "the converter" or "the workbench" it means what that tool
> did at the time, not a directory that still exists.

**Fixed so far:** D17 and D18 (0.6.1); D2, D15 and D16 (0.6.2); D1 and D3
(0.6.3); D4, D5, D9, D12 and D13 (0.6.4); D6 and D8 (0.7.0); D14 (0.7.4);
D10 (0.8.0); D19 (0.8.1); D20 (0.8.2); D21 (0.8.4); D1 in full
(0.8.5, and on the pattern path in 0.8.6); D22 (0.8.7); D7 in part
(0.8.9); the block grammar's invented captions (0.9.0); D23 (0.9.1);
D24 (0.9.4); D25 (0.9.5); D26 (0.9.6, and its own
regression in 0.9.7); D27 (0.9.8); D28 (0.9.9, 15 September 2026); the two conversion paths collapsed into
one reader (0.10.0, 16 September 2026).
Their sections below are kept and marked, because the reasoning is the record of
why the code looks the way it does now. **D7 and D11 remain open** — see the
list at the end.

Reproduce every number below with:

```bash
python scripts/corpus_report.py            # the summary
python scripts/corpus_report.py --detail   # every affected statement
python scripts/corpus_report.py --drift    # only the tags that no longer hold
```

## Headline

| | at 0.6.0 | now (0.8.9) |
|---|---|---|
| statements | 117 unique (132 before de-duplication), 11 sections | — |
| converted cleanly | 67 (60%) | **90 (77%)** |
| converted with values **silently** dropped | 36 (32%) | **0** |
| converted, and told the cataloguer what it dropped | 3 | **22** |
| produced no fields at all | 6 (5%) | **5 (4%)** |
| detector clusters | 55, 39 of them singletons | **44, 31 singletons** |
| one shape split across several clusters | 45 statements, 15 confirmations | **0** |
| statements a pattern could claim only part of | 37 (33%) | 64, none convert |

The silent-loss column is the one to watch, and the clean rate is not. Statements
moved *out* of "clean" in both directions on purpose: ten refused outright rather
than convert a third of themselves (D1, D3), and twenty more convert while naming
a value they could not place. Both are the same trade — less written, and what is
written is true. The clean rate rose again in 0.7.0 for a different reason: four
statements the model simply could not express now convert whole (D6, D8).

Seven of those ten refusals have since come back as conversions rather than as
refusals reversed. 0.8.5 reads a discontinuous list properly — one 863 per run,
with `$w g` marking the gaps — so "v. 19 nos. 1, 3, 5, 7-12 (Jan, Mar, May,
Jul-Dec 1915)" is four fields, not one refusal and not one wrong field. The
refusal was the right answer for as long as reading the statement was beyond the
parser, and the order matters: refuse first, read later.

The number that matters is not the refusals. It was the **36 silent losses**.
There are now **none**: the last one was the run-on whose day-level dates the
block grammar discarded, and 0.8.0 records them (D10). Every value the corpus
states is now either encoded or named — which is the whole claim the bounded-
errors framework makes, held on 112 real statements.

The partial-match count *rose*, from 37 to 64, and that is not a regression:
the generated expressions became broader in 0.6.4, so more of them now overlap
more statements. None convert on a partial match, because 0.6.1 made that
impossible — the number measures how much work that rule is doing.

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

### D1 — a discontinuous list is truncated at its first comma · 7 statements · **REFUSED in 0.6.3, READ in 0.8.5**

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

**Fixed in 0.6.3** by moving that guard out of the captionless branch, so it
applies whenever the match does not account for the whole unit. The statement
now refuses, and says how far it got:

> Read 'v. 19 nos. 1' but could not account for ', 3, 5, 7-12 (Jan, Mar, May,
> Jul-Dec 1915)' — nothing was converted from this statement rather than convert
> part of it.

This does not *parse* a discontinuous list — that is still open, and a bigger
job. It stops the tool destroying one. A refusal keeps the 866 and puts the
statement in the review queue, which is where a shape the parser cannot read
belongs.

**Read in 0.8.5.** MARC 21 records a gap in holdings as another 863 under the
same 853, so the four runs above are four fields:

```
853 31 $8 1   $a v. $b no. $i (year) $j (month)
863 40 $8 1.1 $a 19 $b 1    $i 1915 $j 01     $w g
863 40 $8 1.2 $a 19 $b 3    $i 1915 $j 03     $w g
863 40 $8 1.3 $a 19 $b 5    $i 1915 $j 05     $w g
863 40 $8 1.4 $a 19 $b 7-12 $i 1915 $j 07-12
```

The converter needed nothing for this. The same holdings written out longhand —
`v. 1 no. 1 (Jan 1990), v. 1 no. 3 (Mar 1990)` — already converted to two 863s
sharing one 853 and one `$8`, because `convert_record()` has treated a gap as
another 863 since 0.5.0. So the compact form is *expanded* into the longhand one
and handed to the unit parser, rather than given a grammar of its own:
`_expand_distributed_list()` rewrites one statement as several, and everything
the unit parser knows about captions, combined issues and seasons applies
unchanged.

What the expansion has to get right is the pairing, and it refuses rather than
guess:

- The two lists must be the same length. Three issue runs against two months
  means the statement was not understood, and filing holdings under the wrong
  dates is exactly the kind of error nothing downstream could detect.
- A single **year** is the one exception: `(1915)` is stated once for every run
  and applies to all of them, and `1915/16` is the same — one publication year
  written across the turn of one.

  **Corrected in 0.8.8.** The rule was first written as "a single bare *year*",
  and the regex behind that admitted a year *range*, so
  `v. 19 nos. 1, 3, 5 (1982-1994)` wrote `$i 1982-1994` onto each of three
  863s — each one then claiming a single issue spans twelve years. A range
  belongs to the statement as a whole and to no run in it. No corpus statement
  has that shape, which is why the audit said nothing; it was found by working
  out what D7's `8,13,15,17,19,20-(1982-1994)` ought to produce.

  The correction is not a refusal. The enumeration is unambiguous and is kept;
  only the chronology has nowhere to go, so it is named — the same treatment
  every other readable-but-unplaceable value gets (D2, D16). That also relaxes
  the old refusal of `(Jan 1915)` for two runs: it now converts the issues and
  names the date.
- A year stated once at the end covers every item before it, read right to left,
  so `(Jan, Mar, May, Jul-Dec 1915)` gives all four runs 1915 while
  `(Nov 1915, Jan 1916)` gives each its own.
- A chronology list has to be homogeneous — months and seasons throughout, or
  years throughout. A mixed one is how the American convention writes a *single*
  date: `(Apr 18, 1996)` splits into `Apr 18` and `1996`, and reading that as two
  items would break one date into two holdings runs.
- Every expanded statement has to parse. All of it or none of it, for the same
  reason the refusal existed: the 866 is removed once anything has been written
  from it.

`$w` is new — the break indicator, saying what the break before the next 863 is.
`g` is a gap break: parts lacking, or a break whose cause is not known, which is
the honest reading of a cataloguer writing `nos. 1, 3`. `n`, a non-gap break
(parts never published, or a discontinuity in the numbering itself), is defined
in `holdings_parser` and never written: nothing here can tell one from the other,
and a wrong code is a claim about the collection. Runs that follow straight on —
`nos. 1, 2, 3` — have no break to indicate and get nothing.

`$w` is set only where the *statement* shows the break. Two separate 866s on one
record may well have a gap between them too, but that is a reading of the record
rather than of the statement, and it is a different decision.

The corpus moves from 83 clean to 90, and from 13 statements producing no fields
to 6. The refusals that remain are D3's designations, D7's captionless
statements, and the two by-design declines.

**The pattern path, in 0.8.6.** The Workbench reaches the same statements by a
different route, and both halves of that route were wrong.

`split_multi_range()` cut the statement at every top-level comma that looked like
a separator, so the confirm step was offered `v. 19 nos. 1`, `3`, `5` and
`7-12 (Jan, Mar, May, Jul-Dec 1915)` as four shapes — two of which mean nothing
on their own. `split_statement()` in `pattern_bridge` now asks the parser's rule
first and keeps a list whole. The rule lives in `holdings_parser` and the
detector stays independent of it: `pattern_detector.py` imports nothing but the
standard library, which is worth keeping, so the Workbench — which already has
both in scope — is where the two meet.

A confirmed pattern then matched the whole statement and converted it to one
compressed 863. A `GroupRole` carries a boundary and a level but no notion of
*which run* a capture opens, so the pattern paired the first value with the last
and sent everything between to "not encoded" — two of the statement's twelve
assertions kept, ten named as dropped. That is within the bounded-errors rule and
still much worse than the parser's four fields. `build_parse_result()` now stands
aside for a discontinuous list and lets the parser read it.

Two things that had to be preserved. A pattern marked **skip** still claims the
statement: skipping is a decision about what the cataloguer will handle by hand,
and standing aside would convert the very statement they asked to be left alone.
And the cataloguer is **told** — they confirmed a pattern, and a pattern that
silently goes unused is the failure mode this whole log keeps finding, so the
conversion says which pattern matched and why the parser read the statement
instead.

Supporting runs inside a pattern is still open, and is a real model change:
`GroupRole` would need a run index, the confirm step would need to show it, and
the library format would have to carry it. Nothing here is a workaround for that
— the parser reads these statements properly, and the pattern path's job is to
not get in the way.

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

### D3 — a designation between enumeration and chronology truncates the parse · 3 statements · **FIXED in 0.6.3**

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

**Fixed in 0.6.3** by the same one-line move as D1: these now refuse and name
the text they could not account for, rather than writing `$a 58` and deleting
the 866.

Widening the guard also surfaced a fourth statement nobody had noticed.
`v.7/8(1996:Jul./Aug.)` in `data/messy_holdings.mrc` was matching only `v.7` and
dropping the rest in silence, because `vol_num` accepted a hyphenated range but
not a combined volume — while `iss_num` had always accepted both. Giving
`vol_num` the same character class converts it properly: `$a 7/8 $i 1996
$j 07/08`. It took a test failure to find, which is the argument for the
committed fixtures.

### D4 — a day inside the date voids that boundary's chronology · 1 statement · **FIXED in 0.6.4**

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

**Fixed in 0.6.4** by giving `_parse_chron_single()` the one pattern it lacked.
The day is still not encoded — 863 `$k` holds one and nothing here models it —
but it is now named, and the month and year survive: `$i 1996-1997 $j 04-12`.
That resolves the inconsistency in the block grammar's favour rather than
inventing day support.

### D5 — chronology wording that is not a code is written into a coded subfield · 3 statements · **FIXED in 0.6.4**

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

**Fixed in 0.6.4** in two halves, because two different things were going wrong.

*One was a gap in the table.* `Sum` is Summer and `Spr.` is Spring; the codes
table held only the full words, so ordinary abbreviations fell through to the
prose path. Adding them converts `2018: ([Sum])` correctly rather than
salvaging it.

*The rest is genuinely uncodeable.* `_is_codeable()` now checks a chronology
value against the codes MARC defines before it is written, and a value that
fails is left out and named. `Buyers Guide` is not chronology at all, and
`Late Summer` is not a season MARC has a code for. Note that this drops the
whole of `11/12-Late Summer`, valid half included: under the one-sided rule
from 0.6.3 the `11/12` could not have been written on its own either.

`Buyers Guide` is a different problem wearing the same clothes: it is not
chronology at all. It is a named issue that happens to sit in the parenthesis
where chronology normally lives.

### D6 — statements captioned at issue level only are refused outright · 3 statements · **FIXED in 0.7.0**

```
no. 26 (May 1994)-no. 37 (May 2000)    ->  nothing
no. 41 (May 2002)                      ->  nothing
```

`_ENUM_CHRON_RE`'s enumeration block was `(vol_cap)? vol_num (iss_cap iss_num)?`.
The issue group could only follow a number, so an issue-first unit had no path
through the grammar at all and the statement failed whole.

**This is not the "captionless" family, and I would not file it with them.** The
caption is right there — `no.` — and it is unambiguous. A serial numbered
continuously by issue with no volume level is a completely ordinary thing
(monographic series, numbered reports, many newsletters). Of the six refusals in
this corpus, these three are the ones worth fixing; nothing has to be guessed.

**Fixed in 0.7.0**, by removing the thing that made it a defect: enumeration is
no longer three named levels (volume, issue, part) but an ordered list of any
depth, and a caption is a *word*, not a level. MARC 21 agrees — 853 `$a`–`$f`
carry captions "in descending order of significance" and say nothing about
which words go in them, so `$a no.` is as ordinary as `$a v.`

```
no. 26 (May 1994)-no. 37 (May 2000)
  853 31 $8 1 $a no. $i (year) $j (month)
  863 40 $8 1.1 $a 26-37 $i 1994-2000 $j 05-05
```

Three consequences worth stating, because they change more than these three
statements:

- **Depth is no longer capped at three.** `Series 1, v. 6 no. 1 (Summer/Fall
  1992)` now fills `$a ser. $b v. $c no.`, and the standard convention has room
  for six levels before it runs out of subfields — at which point the levels it
  cannot place are named in a warning rather than dropped.
- **Position, not the word, decides the subfield.** The Workbench's confirm step
  offers the familiar words as suggested captions and lets a cataloguer override
  both the caption and the level a value sits at; the level defaults to the
  order the values appear in, because that order *is* the hierarchy.
- **A new guard came with it.** One 853 is the caption pattern for every 863
  linked to it, so two statements on one record that number by different
  hierarchies cannot share one. `v.1(1990), no.5(1995)` would have written the
  `5` into `$a v.` — read downstream as volume 5. It is now left out and named.

### D7 — genuinely captionless statements · 3 statements · **1 of 3 READ in 0.8.9**

```
8,13,15,17,19,20-(1982-1994)
50th Anniversary Issue (2017)
Special Issue (October/November 1995)
```

These the monolith never handled either. Nothing in `8,13,15,...` says whether
those are volumes, issues or years, and refusing was the documented behaviour —
the same argument the README makes about `?: 16`. A cataloguer supplies the
level; the parser cannot.

**The question turned out to be the wrong one.** The parser does not need to know
whether they are volumes. It needs to know they are an enumeration level, and
position already says which: they are the most significant one, and the only one.
What was missing was a way to write a level down without naming it — and MARC 21
has one. From the 853-855 documentation: where a level has no caption on the
piece, a caption may be invented and bracketed, *or an asterisk used in place of
data*, to achieve full correlation. Full correlation is **required** wherever the
863 is compressed, which is every field this tool writes.

So `NO_CAPTION = "(*)"` (0.8.9), and the statement reads:

```
853 31 $8 1   $a (*)
863 40 $8 1.1 $a 8   $w g
863 40 $8 1.2 $a 13  $w g
863 40 $8 1.3 $a 15  $w g
863 40 $8 1.4 $a 17  $w g
863 40 $8 1.5 $a 19
863 40 $8 1.6 $a 20-
! '1982-1994' was left out: it is stated once for all 6 runs …
```

`19` runs straight on into `20`, so no `$w` there; `20-` is still being received.
The date range is the 0.8.8 case — it spans the statement and belongs to no run
in it — so it is named rather than copied onto all six. That is the whole
statement accounted for: six runs encoded, one range named, nothing silent.

**The larger thing this exposed.** The asterisk was not really needed for D7. It
was needed because the converter was *already* inventing captions, silently, in a
place nobody had audited. `DEFAULT_ENUM_CAPTIONS = ("v.", "no.", "pt.")` filled
any level a statement did not caption, so `39 no 1 (Spring 1995)` produced
`853 $a v. $b no.` — asserting that 39 is a volume on the strength of position
alone. Very probably true. Not something to write into a record as though the
piece had said it, and the fourth appearance of this log's recurring shape.

The constant split in two. `NO_CAPTION` is what the 853 gets when nothing else is
known; `SUGGESTED_ENUM_CAPTIONS` keeps `v./no./pt.` as what to *offer* a
cataloguer, which claims nothing. Precedence is unchanged and is the point: a
caption supplied by hand wins, then one the statement wrote, and only then the
asterisk. Both settings dialogs now say so, and note that a supplied caption can
be bracketed — `[v.]` — to show in the record that it was supplied.

`caption_slot()` had to learn to read the asterisk back. It required a caption to
begin with a letter, so `(*)` returned None: the tool would have written an 853 it
could not read, and a record it had already converted would have stopped
conforming to its own field on the next run.

**Still failing, and rightly.** `50th Anniversary Issue (2017)` and `Special
Issue (October/November 1995)` have no enumeration at all — a phrase and a date,
with nothing to caption. The asterisk does not reach them. The Workbench's confirm
step remains the mechanism that could, since a human says once what the captured
values mean.

**The block grammar, in 0.9.0 — and what was hiding behind it.** The
chronology-first grammar hard-coded `v.` and `no.` on the same positional
reasoning, for 5 corpus statements. Removing them exposed a defect the words had
been covering:

```
N1984: (2 (1))M1985: 2 (2 [summer])
  -> 853 $a no. $b no. $i (year) $j (season)
     863 $8 1.1 $a 2 $i 1984
     863 $8 1.2 $b 2 $i 1985 $j 22
     ! 'v.2' was left out: this record's 853 calls that level 'no.' … Split the
       statements that number differently onto their own records.
```

Both `2`s are the same level of the same serial, and they are in different
subfields. The 853 declares two levels and calls them both `no.`. And a volume
the statement *does* state was dropped, with advice to split records that "number
differently" — they do not number differently; one block omits a level.

**Cause.** `year: A (B [chron])` fixes the positions — `A` is the higher level,
`B` the lower — and the parser appended whichever were present in turn, so a
block with no `A` slid its `B` into position 0. Position is the level, so that
made an issue into a volume. The captions were the only thing that showed it, and
they showed it as the nonsense `$a no. $b no.`. Remove them first and the
misplacement would have gone silent.

**Fixed** by holding the place with an empty level, and dropping that placeholder
again when no block in the statement ever fills it — otherwise `1993: (1 [Feb])`
would declare a level the serial does not have. So:

```
1993: (1 [Feb])                      -> 853 $a (*)          863 $a 1
2019: (1-6 …)2020: (7-12 …)          -> 853 $a (*)          863 $a 1-6 / $a 7-12
N1984: (2 (1))M1985: 2 (2 [summer])  -> 853 $a (*) $b (*)   863 $b 2 / $a 2 $b 2
```

The dropped volume is recovered and the spurious warning is gone.

This is the same lesson as D21 and the year-range regression, from the other
direction: the invented captions were wrong, *and* they were the only thing
making a worse bug visible. Taking them out without looking would have traded a
loud error for a quiet one.

### D8, D9, D11 — smaller things, all warned or by design (D9 **FIXED in 0.6.4**)

- **D8** `Series 1, v. 6 no. 1 (Summer/Fall 1992)` — the series designation was
  dropped, but with a warning naming it. **Fixed in 0.7.0**, in two parts. `ser.`
  became a caption word, so the designation parses; and `_split_ranges` no longer
  breaks the statement at that comma. The test is whether both sides number by
  the same caption: a repeated caption is a second range (`v. 1, v. 5 (1994)` is
  two ranges, and that is how a cataloguer writes a gap), a caption appearing
  only on the left is a heading. It now encodes whole, three levels deep:
  `853 $a ser. $b v. $c no.` over `863 $a 1 $b 6 $c 1`.
- **D9** `N1984: (2 (1))M1985: 2 (2 [summer])` — `_BLOCK_RE`'s body allows one
  nesting level, and the inner `(1)` was lost without comment. **Fixed in
  0.6.4**: the nested group is named rather than dropped in silence. Its meaning
  is local to whoever wrote it, so guessing would be worse than saying so.
- **D11** `N?: 1 (4)1984: ...` — unexplained markers, warned by design.
- **D10** the long `ONE` run-on — the detector's complexity guard declines it,
  which is intended and stands. The parser also dropped the day from each
  bracketed date, which was **not** intended and is **fixed in 0.8.0**: days go
  to 863 `$k`. That was the corpus's last silent loss, and it was fourteen
  dates from one record. See "Day-level chronology" below.

### Day-level chronology — **ADDED in 0.8.0**

`_bracket_chron_unit()` matched the month word at the head of a bracketed date
and returned it, so `[Jan 28-Dec 29]` became `01`–`12` and the two days went
nowhere. Its docstring said "trailing day dropped" and nothing on screen did.
On the `ONE` run-on that is fourteen dates from one record — the corpus's last
silent loss, and the reason the headline column stood at 1 rather than 0 for
four versions.

MARC 21 863 numbers its chronology levels: `$i` first, `$j` second, `$k` third.
A serial captioned `(year)(month)(day)` puts the day in `$k`, so there was a
right answer available all along, and `EnumChron.day` already existed as a
field nothing populated. 0.8.0 populates and writes it:

```
1983: 5 (7-30 [Jan 28-Dec 29])
  853 31 $8 1 $a v. $b no. $i (year) $j (month) $k (day)
  863 40 $8 1.1 $a 5 $b 7-30 $i 1983 $j 01-12 $k 28-29
```

Four things this had to get right, none of them about the day itself:

- **`[Jan 5-Jan 26]` is one month and two days.** The block grammar dropped the
  end boundary whenever the months matched, which would have thrown away the
  second day. It now keeps the end when *either* level differs.
- **The one-sided rule applies unchanged.** A day only one end gives is dropped
  and named, exactly as a month is, and for the same reason: a lone `$k 18`
  pairs positionally with `$i` and `$j`, claiming the run ends on the 18th as
  well as beginning on it. This closes the other half of D4, which recorded
  `Apr 18, 1996` to the month and named the day — the day is kept now when both
  ends give one, and named when only one does. The rule the cataloguer set for
  chronology in 0.6.2 needed no amendment to cover a third level, which is a
  fair sign it was the right rule.
- **`(day)` is not an enumeration caption.** `caption_slot()` tests for "year",
  then "season"/"month"/"chron", then falls through to "this is an enumeration
  caption". "(day)" is short and wordlike, so without an explicit test an
  existing 853's `$k` came back as a *numbering* level. Caught by writing the
  test, not by reading the code.
- **A convention need not have a day.** The house convention reproduces local
  records with no precedent for a day subfield, so it has none. Inventing a
  code would be worse than saying the day cannot be placed, so the 863 leaves
  it out, the 853 declares no caption it will not fill, and the record says
  which day was lost and where MARC would put it.

### D19 — a year split across the turn of one is unreadable · **FIXED in 0.8.1**

Reported from real use, not found by the corpus, which had no example of it.

```
v. 12 no. 4 (Winter 1996/97)
  was: 853 31 $8 1 $a v. $b no. $i (year) $j (season)
       863 40 $8 1.1 $a 12 $b 4                          <- no year, no season
  now: 863 40 $8 1.1 $a 12 $b 4 $i 1996/1997 $j 24
```

A serial whose winter issue straddles the new year is numbered `1996/97` as a
matter of course. Every year alternative in `_parse_chron_single()` wanted
`\d{4}`, so `Winter 1996/97` matched none of them, fell through to the
give-up branch that returns the raw text as a year, and was then rejected by
the converter as wording a coded subfield cannot hold. **The season went with
it** — the statement lost both levels of its chronology, and the report counted
the whole group as one dropped value rather than two.

Worse in a range. `v.1(Spring 1996)-v.5(Winter 1996/97)` produced `$j 21` and
nothing else: an 863 asserting the run was *all Spring*. The one-sided rule was
working exactly as designed — the end boundary genuinely gave no season, because
it had failed to parse — which is a good illustration of a guard being no better
than the thing it guards.

MARC records the pair slash-joined in `$i`, the same way the tool already
slash-joins a combined month in `$j`, so `_YEAR_VALUE_RE` accepted `1996/1997`
without change. The fix is one shared year token across the parser, the
detector's tokeniser and the expressions it generates, plus `normalise_year()`
to write the two-digit half out in full — taking its century from the first
half and rolling forward where it must, so `1999/00` is `1999/2000`.

Two things fell out of it:

- **The detector used to read `Winter 1996/97` as four tokens** — season, year,
  free text, number — so a statement carrying one formed its own cluster and
  the confirmation screen asked what the "97" meant. It is one value now, and
  `(Spring 1996)` and `(Winter 1996/97)` cluster as **one** pattern.
- **The corpus audit needed telling.** It compares every number in the source
  against the generated fields, so the expanded `1997` read as a dropped `97`.
  Month *words* were already compared as codes for the same reason; a split
  year is the same kind of normalisation and is now expanded before the
  comparison.

### D20 — the detector emits expressions its own Test button would refuse · **FIXED in 0.8.2**

Found while fixing D19.

`MAX_PATTERN_TOKENS = 40` was calibrated on the two private `.mrc` files at
"15–45 regex characters per token", to keep generated expressions under the
2,000-character cap `/api/test-regex` enforces. The corpus's worst statement
costs **84 characters per token** and generates **2,384** — because a single
`CHRON` group is 415 characters on its own, and that statement has four.

It has been over the cap all along: 2,374 on `main` before D19's work, which
added 19. Nothing caught it because `test_every_generated_regex_is_testable`
runs against the two committed synthetic `.mrc` files only, and the worst they
produce is 1,980. The corpus was never in its reach.

**Fixed by measuring the expression instead of estimating it.** The token count
was only ever a proxy for length, and the proxy fails because token kinds cost
wildly different amounts: a `CHRON` spends the month alternation twice, about
180 characters, where a `NUMBER` spends 25. The worst statement is 25 tokens —
comfortably inside `MAX_PATTERN_TOKENS = 40` — and 2,384 characters.

`detect_patterns()` now builds the regex and checks its actual length against
`MAX_REGEX_CHARS`, declining the cluster if it is over. The invariant holds by
construction rather than by calibration: whatever the detector emits can always
be tested and stored, whatever future change alters the cost of a token. D19
would have been caught by it automatically.

The month alternation was deliberately *not* shortened. `(?:Jan|Feb|…)[a-z]*`
would save about 100 characters per copy, but it matches "Janissary 1996" as a
date, and quiet wrongness is the thing this project exists to avoid.

**The cap was raised to 4,000 in 0.8.3**, at the cataloguer's request, and the
reasoning that had kept it at 2,000 turned out to be wrong. It was described as
a ReDoS bound on a public endpoint — but length is nearly uncorrelated with
backtracking risk:

```
^(\s*\w+)*$          11 characters, hangs on a 50-character input
the 2,384-char one   no nested quantifier, no unbounded .*, two bounded
                     lazy spans; searches an adversarial 500-char string
                     in under a millisecond
```

A length cap turns away *long* expressions, not *dangerous* ones. What actually
bounds the damage on that endpoint is the input side — 2,000 statements of 500
characters — and what would end it is a match timeout, which the tool did not
have at any cap value. **Added in 0.8.7** — see D22.

So the cap is what it always really was: the point past which an expression is
too unwieldy to read, edit or test. 4,000 admits everything the corpus produces
(worst: 2,384) with headroom for the per-token cost to grow again the way D19
grew it. The length guard is now a backstop behind `MAX_PATTERN_TOKENS` rather
than the binding constraint, which is the right relationship: the token count
decides "too idiosyncratic", and the measured length guarantees the invariant
whatever future change alters a token's cost.

One corpus cluster came back as a result: `v. 19 nos. 1, 3, 5, 7-12 (Jan, Mar,
May, Jul-Dec 1915)`, which now generates a usable pattern. The converter refused
the statement as a discontinuous list at the time, so the pattern path was the
only route it had; 0.8.5 gave it the better one.

`MAX_REGEX_CHARS` moved to `pattern_detector.py` and is imported by
`pattern_library.py`. Two copies of a safety limit drift.

One thing the fix exposed. The declined card read "These statements are 25
parts long, which is past the point where a single expression can describe
them" — false, with the token ceiling at 40, and a cataloguer would reasonably
wonder why 25 was too many when the pattern above it had 30. The card had only
ever had one reason to give because there had only ever been one limit. Each
guard now carries its own sentence, and the cluster declined on length says so:
"The expression for these statements comes to 2,384 characters, past the 2,000
that can be tested here — months and seasons are expensive to describe, and
these statements carry several."

One lossless saving was taken while measuring: the generated expressions were
writing the whitespace separator twice between most parts, because each branch
appends `\s*` after its group and the separators already carry their own.
`_join()` drops the redundant one. Same language, ~24 characters back on a
chronology-heavy pattern.

### D21 — a range that closes at a level it did not open reads backwards · **FIXED in 0.8.4**

Found while working out what the tools do with a gapped statement.

```
v. 12 no. 1-no. 6 (1990)
  -> 853 $a v. $b no. $i (year)
     863 $a 12-6 $i 1990
     ! Only the start of this range gives a no level (1) ... it was left out.
```

`$a 12-6` is volume 12 through volume 6. The statement says issues 1 through 6
of volume 12. And the warning, which is the cataloguer's only view of what
happened, describes a *different* field: it reports the issue level as left out
for want of an end value, while that end value is what is sitting in `$a`.

**Cause.** Position in `EnumChron.enum` is the level, which is exactly right
while both boundaries write the same number of levels. This statement writes two
and then one, so `no. 6` sat at position 0 opposite `v. 12`, and
`_hierarchy_values()` paired them because pairing by position is all it does.
Nothing anywhere compared the two ends' captions: `enum_captions()` takes the
first caption it finds at each level, so the start's `v.` filled position 0 and
the end's `no.` was never consulted.

The same hole was doing worse damage on the pattern path, where
`assign_levels()` numbers each boundary's levels from zero independently and
documents that it does — `v. 19 nos. 1, 3, 5, 7-12 (Jan, Mar, May, Jul-Dec
1915)` produced `$a 19-3`.

**Fixed in 0.8.4** in two halves, because the case splits in two.

Where the captions settle it, they settle it: `HoldingsRange.align_boundaries()`
slides the shorter boundary down to the one offset its captions fit, padding
with empty levels. `v. 12 no. 1-no. 6` now gives `$a 12 $b 1-6` with no warning,
because nothing was lost. It runs at construction and again after the parser
fills a range in, and is idempotent so that both are safe.

Where they do not — a range opening `v.` and closing `pt.`, or a caption that
fits at two levels — nothing moves, and `_unpairable_end_levels()` drops the
closing value and names it:

> 'pt.4' was left out: this range opens at a 'v.' level and closes at a 'pt.'
> level, so which level '4' closes cannot be told from the statement. A
> compressed 863 pairs the two ends level by level, and there is no pairing for
> this one.

No corpus statement exercises either half — this was found by reasoning about
the model, not by the audit, and the audit's numbers are unchanged. That is
worth recording on its own: the corpus is 117 statements from one collection,
and "no statement here does that" is not "no statement does that". The two
boundaries of a compressed 863 are the whole content of the field, and until now
nothing checked they were describing the same hierarchy.

### D22 — a hand-edited expression can hang the server · **FIXED in 0.8.7**

The loose end D20 left. Python's `re` cannot be interrupted: there is no timeout
argument, and a match in progress ignores signals until it returns. So an
expression a cataloguer has edited by hand — `^(\s*\w+)*$` is eleven characters
— takes a gunicorn worker out of service permanently. With `--workers 2`, two of
them take the site down until someone restarts it.

`MAX_REGEX_CHARS` never protected against this and, since 0.8.3, says so.

**Fixed in 0.8.7** by running the match in a **child process** the request kills
when the budget runs out (`pattern-detector/regex_budget.py`). Two alternatives
were considered:

- `signal.setitimer` only works in the main thread. Gunicorn's sync workers
  would be fine, but Flask's development server is threaded by default and the
  guard would silently do nothing there. A safety measure that is present in
  some configurations and absent in others is worse than an honest one.
- The third-party `regex` module takes a `timeout=`. Adding a dependency to a
  tool a librarian installs with `pip install flask pymarc gunicorn` costs more
  than the file does.

The budget is five seconds, from measurement rather than taste. The largest
payload any endpoint accepts — the longest expression the detector generates
(2,485 characters after 0.9.4) against 2,000 copies of a 500-character
adversarial string — takes 24 ms of matching and 223 ms end to end, the
difference being the child's startup and the JSON in both directions. Five
seconds is about twenty times
that, and the trade is asymmetric: three more seconds of waiting costs a
cataloguer very little, and a good pattern wrongly refused for being slow costs
them the pattern. `MARC_MATCH_BUDGET` overrides it.

**Where it is applied.** Every door a user-supplied expression comes through:

| door | what it protects |
|---|---|
| Pattern Detector `/api/test-regex` | the Test button |
| Workbench `/api/test-regex` | the Test button on the confirmation card |
| Workbench `/api/pattern-preview` | the candidate run through a whole conversion |
| Workbench `PUT /api/patterns`, `/api/patterns/import` | the library |

The library is the one that matters most, and it is the one a timeout on the
Test endpoint alone would have missed. A pattern on the Test screen is bounded
by the request it runs in; a pattern *stored* is run by every conversion
afterwards, against every statement of every record, with nothing able to stop
it — and nothing obliges a cataloguer to press Test before confirming. Storing
one is now checked on the way in, against the session's own statements plus a
few fixed strings that provoke the classic runaway shapes. Only expressions the
session has not already stored are tried, so reordering and removing — the same
PUT as confirming — cost nothing.

Two things worth being honest about.

The library check is a **screen, not a proof**. An expression that finishes on
these statements may still blow up on one nobody has tried. The only guarantee
this gives is the one `match_statements()` gives: whatever runs, stops.

And the message matters as much as the guard. A cataloguer told only that
something timed out concludes the tool is broken. The cause is nearly always one
shape — a repeat inside a repeat — so the message names it and says what to do,
which is the difference between that and a fixed expression.

One client-side bug fell out of it. `saveLibrary()` set `library = data.patterns
|| []` on every response, so a refusal — which returns no `patterns` — emptied
the screen's copy of a library the server had in fact left untouched. The
cataloguer would have seen every confirmation disappear.

### D23 — uploading a file deletes the pattern library · **FIXED in 0.9.1**

Reported from real use, on the wellformed file with a 120-pattern library
confirmed in an earlier session: every record converted with the standard parser
while the sidebar said 120 patterns were confirmed. Clearing the library and
detecting again made the patterns apply.

**Cause.** `_purge_old_uploads()` swept *everything* in the upload directory past
`UPLOAD_TTL_SECONDS` (6 hours), and the pattern library is a `.json` file in that
same directory. The sweep runs on `_save_file()` — which is what an upload calls.
So:

1. The page loads and reads the library: **120 patterns**, shown in the sidebar.
2. The cataloguer uploads their `.mrc`. The upload's own sweep deletes the
   library, because it was written yesterday. The `.mrc` survives: it is rewritten
   immediately afterwards.
3. Conversion loads a library that is now empty and falls through to the parser
   for every statement.
4. The sidebar still says 120, because nothing re-read it.

Reproduced exactly through the HTTP API by backdating the stored files, which is
also how the two regression tests work.

**Fixed** by giving the two kinds of stored file their own lifetimes. An upload is
the cataloguer's *data* and still goes after six hours — that limit is a
deliberate choice about not keeping holdings on a server. A library is their
*work*, and keeping it for thirty days is the least that makes sense when
confirming one takes an afternoon. Its age is now measured from last **use**
rather than last write, because reading is almost all a library gets: converting
with the same hundred patterns every week never rewrites them.

Two things alongside it. `_load_library()` discarded the errors from
`from_export()` — a pattern that stops loading takes every record it used to read
with it, and the screen looks identical — so they are logged now. And the client
re-reads the library after an upload, so a count on screen can no longer be one
conversion will not use.

**What this says about the rest of the log.** Every other defect here is about
what the tools write into a record. This one is about a file being deleted by a
housekeeping routine that had no idea what it was deleting, and it cost more
holdings than most of them: 120 confirmations, and then a whole file converted by
the fallback path. It was invisible to every test in the suite and to the corpus
audit, because both exercise conversion with a library that was just built. Only
the passage of time exposes it — which is the one thing a test suite never has
and a cataloguer always does.

### D24 — a combined designation is captured twice, and the halves are transposed · 6 statements · **FIXED in 0.9.4**

Found while measuring whether a run-index model was warranted. It was not; this
was sitting next to it.

```
v. 34 no. 8/9-v. 35 no. 23/24 (Apr 18, 1996-Dec 1997)

  start_vol = 34  level 0   ✓
  start_iss = 8   level 1   ✓
  end_iss   = 9   level 0   ← an issue, in the volume slot
  end_vol   = 35  level 1   ← a volume, in the issue slot
  end_iss_2 = 23  ignore
  end_iss_3 = 24  ignore

  parser  -> 863 $a 34-35 $b 8/9-23/24 $i 1996-1997 $j 04-12
  pattern -> 863 $a 34    $b 8         $i 1996-1997 $j 04-12
```

The end volume is gone and the combined issue truncated to its first half. Note
that `infer_roles()` was doing exactly what it documents — numbering levels by
order of appearance, taking the second value at a level as the end — which is
right for a value that really *is* two. The fault is upstream: `no. 8/9` is one
issue, and the detector's NUMBER token matched a bare `\d+`, so the slash became
free text and the halves became two captures.

**The slash and the hyphen are different operators, and conflating them is the
trap.** The first draft of this fix absorbed both, which broke the commoner
shape — a cataloguer caught it:

```
v.1-5(1990-1994)                one unit   1-5 is a range *spanning the
                                           statement*: two endpoints
v. 23 no. 3-4-v. 29 no. 3-4     two units  3-4 is issues 3-4 *of v. 23*:
                                           one value inside a unit
```

A slash is part of a designation and always binds. A hyphen means *through*, and
whether it joins a value or spans the statement depends on whether some **other**
hyphen divides the statement into units — a fact about the whole token stream,
which no tokeniser regex can see. So:

- the tokeniser binds `/` only, exactly as YEAR has absorbed `1996/97` since
  0.8.1 and CHRON absorbs `Jul/Aug`;
- `_merge_ranged_numbers()` runs afterwards and joins `NUMBER - NUMBER` **only**
  in a statement that has a unit separator — a hyphen after a `)` or before a
  caption, which is what every real divider looks like;
- the emitted capture group is deliberately *wider* than the tokeniser and
  accepts a hyphenated value. The tokeniser decides how many captures a
  statement has; the group decides what one capture may hold. Leaving the hyphen
  out of the group was caught by the corpus report: a cluster stopped matching
  its own members, because a merged `3-4` had no group that could hold it.

**Result.** Six corpus statements where the pattern path and the parser disagreed
now agree, and **none** newly disagree. `v.1-5(1990-1994)` is untouched, and so is
every other existing expectation: the whole suite passed unchanged, where the
first draft had needed four tests rewritten. That contrast is the useful signal —
a fix that has to rewrite tests protecting a deliberate design is usually
arguing with the design rather than fixing a defect.

Clusters fall from 45 to 44 and singletons from 31 to 30. The longest generated
expression grows from 2,384 characters to 2,546 — a NUMBER group costs 54
characters now rather than 25 — still well inside the 4,000 cap, and the figures
quoted in `regex_budget.py` were re-measured rather than left to drift.

### D25 — a level under a ranging one is written when there is only one boundary · 1 statement · **FIXED in 0.9.5**

Found by chasing the last enumeration-level disagreement between the parser and
the pattern path.

```
v. 40-45 no. 4 (1974-Apr 1979)
  parser  -> 863 $a 40-45 $b 4 $i 1974-1979
  pattern -> 863 $a 40-45       $i 1974-1979   + the 4 named as dropped
```

The pattern path was right and the parser was wrong, which is the unusual
direction.

**Cause.** `_hierarchy_values()` has had the rule since 0.6.2 and states it in its
own docstring:

> `"$a 41-43 $b 1"` cannot be read back as v.41:no.1 - v.43:no.1 — it describes
> issue 1 of each of volumes 41 to 43 just as well.

But the rule lived in the branch for a range with *two* boundaries. `v. 40-45
no. 4` states **one**: the parser reads it as a single boundary whose volume
value is itself the range `40-45`. That took the "Single unit: no other end to
disagree with" branch, which is true about disagreement and misses that a level
above can range *within* one boundary — so the value was written.

The pattern path avoided it by accident: the detector captures `40-45` as
`start_vol` and `end_vol`, giving two boundaries, so the existing rule fired.

**Fixed** by applying the same test in the single-boundary branch:
`ranged_above` now carries the ranging *value* rather than a flag, so the note
can quote it, and a lone value under it is dropped and named. A value that is
itself a range still pairs and is untouched — `v. 40-45 nos. 2-5` reads back as
one run, v. 40 no. 2 through v. 45 no. 5.

No inference is involved, and none should be. "v. 40-45 no. 4" might mean a run
ending at v. 45 no. 4, or no. 4 of each volume from 40 to 45; supposing the run
starts at v. 40 no. 1 would be inventing a value the statement never gives. The
two readings are what the notation cannot tell apart, which is exactly why the
value is named rather than placed.

The 853 still declares `$b no.`, because the serial does have an issue level even
though this 863 does not record it — so the "853s declaring a caption their own
863 never fills" count rises from 18 to 19. That count is a watch-list, not an
error, and this is a legitimate entry in it.

### D26 — a qualified season is quietly narrowed to the season · **FIXED in 0.9.6**

The last disagreement between the two paths that was a defect rather than a
difference, and the pattern path's only silent loss.

```
v. 15 no. 6 - v. 23 nos. 2/3 (Nov/Dec 1994 - Late Summer 2002)

  parser  -> 863 $a 15-23 $b 6-2/3 $i 1994-2002
             ! '11/12-Late Summer' is not something a month or season subfield
               can hold …
  pattern -> 863 $a 15-23 $b 6-2/3 $i 1994-2002 $j 11/12-22
             (no warning at all)
```

The record said the run ended in **Summer 2002**. The source says Late Summer.

**Cause.** The detector's CHRON token matches a month or season wherever it
finds one, so `Late Summer` captures `Summer` and `Late` becomes literal text in
the generated expression — text the cataloguer is never asked a question about,
and which then vanishes. Every corpus audit of silent loss has been run against
the *parser*, which is why this sat at zero while the pattern path dropped a
word.

**Why it cannot simply be coded.** From the cataloguer who reported it:

> at my library, the rule we followed was to put whatever enum/chron was printed
> on the actual issue into the textual holdings statement, but in this "Late
> Summer" example it's unclear whether this maps directly as a Summer issue or
> if there is also an "Early Summer" issue then this wouldn't work, i.e.,
> cataloger needs to investigate before making the call

`Late Summer` may be the Summer issue. The serial may equally have an Early
Summer, and coding both `22` would merge two different issues into one. Nothing
in the statement settles it; somebody has to look at the piece.

**Fixed** by putting the qualifier back on the value before it is encoded. That
is all — no new rule: `_is_codeable()` then refuses `Late Summer` exactly as it
refuses it on the parser path, `_note_uncodeable()` names it, and the two paths
produce the same field again.

**And the record is flagged.** `_note_uncodeable()` now sets the `flagged` state,
which is what "Needs attention" on the review screen keys on. A warning alone was
not enough: it shows when a row is opened, and nobody opens a row that looks
converted. `flagged` has meant "fields produced, but not vouched for" since 0.7.4
(D14) — a value the tool could read and could not encode is exactly that, and it
had only ever been set by the enumeration-depth guard.

Two corpus statements are now flagged: this one and `v. 15 (1998 Buyers Guide)`,
where a named issue sits where a date should be. Both are cases where the tool
has done what it can and a person has to finish.

The qualifier test is deliberately narrow — letters running straight into the
captured unit, with only spaces between. A separator or a bracket before it is
not a qualifier, and treating one as though it were would refuse most of the
corpus; `(Spring 1990)`, `(Winter 1986 - Summer 1987)` and
`(November/December 2016 - January 2022)` are all pinned as untouched.

**And it was not narrow enough. Reported from real use, fixed in 0.9.7.**

```
(Jan/Feb-July/Aug 1985)
  0.9.6  -> '01/02-Feb- July/Aug' is not something a month or season subfield
            can hold … so it was left out
  0.9.7  -> 863 … $j 01/02-07/08
```

The character class for a qualifier word was written `[A-Za-z.'\u2019-]`, and the
trailing `-` in it is a literal hyphen. So the text before `July/Aug` — which is
`(Jan/Feb-` — matched `Feb-`, and a **range separator** was read as a qualifier.
Ten of the 136 statements across the two corpora lost their chronology and were
flagged for review: every `Apr-Jul`, `Jan-Jun`, `Jul/Aug-Sep/Oct` there is.

The hyphen is the one character that can be either half of this. `mid-July` is a
qualified month; the `Feb-` of `(Jan/Feb-July/Aug)` is a month and a separator.
What tells them apart is whether the word is *itself* a month or a season, so the
hyphen is captured separately now and `chron_unit_code()` decides.

**The verification gap is the part worth keeping.** `scripts/corpus_report.py
--drift` said "no drift" for both the original defect and the regression, because
the corpus report only ever runs the **parser**. The silent-loss column has read
zero since 0.8.0 and was quoted repeatedly while the pattern path dropped a word;
then the fix broke ten statements through the same blind spot and the same report
said nothing.

`test_the_pattern_path_never_drops_a_chronology_the_parser_keeps` now compares
the two paths directly, on exactly what went wrong both times. It fails on 0.9.6
naming all ten statements. Its one standing exception was `2018: ([Sum])` — the
detector's CHRON token lists `summer` and not the abbreviation `sum`, which the
parser's own table carries — and the set is asserted exactly, so a new one is a
failure and a resolved one is a stale note.

That exception closed in 0.10.0, when the parser became the single reader of
holdings structure and there was no longer a second reading for the abbreviation
to fall out of. `KNOWN_CHRON_GAPS` is now empty, and
`test_the_two_paths_write_the_same_863()` makes the stronger claim the
architecture allows: not that chronology survives, but that both paths write the
same 863, on every statement where both write one. See "One reader, not two".

### D27 — a spaced slash is not read as a separator, and the refusal keeps half a range · **FIXED in 0.9.8**

Carried as an `xfail` since the corpus was adopted, described as "the second
range is silently dropped". It was worse than that.

```
v.1(1990)-v.3(1992) / v.5(1994)-v.8(1997)

  -> 863 40 $8 1.1 $a 1 $i 1990
  !  "Read 'v.3(1992)' but could not account for '/ v.5(1994)-v.8(1997)' —
      nothing was converted from this statement rather than convert part of it."

  needs_review: False    flagged: False
```

The statement names **eight volumes across seven years**. The record gets volume
1, 1990. Three separate things are wrong:

1. **A partial conversion on the default path** — the outcome 0.6.1 exists to
   prevent. `_smart_split_range()` splits at the first hyphen; `_parse_unit()`
   then refuses the end half, which it can read only as far as `v.3(1992)`.
2. **The warning says the opposite of what happened.** It states that nothing was
   converted. `$a 1 $i 1990` was converted. A cataloguer reading it would believe
   the 866 had been left alone.
3. **Neither held nor flagged**, so it never reaches the review queue. With
   `remove_866` on — the Converter's default — the 866 goes and the record is
   left claiming the library holds v. 1 (1990) and nothing else.

**Two causes, fixed separately.**

`_split_ranges()` never learned that a spaced slash separates two ranges.
`split_multi_range()` in the detector has drawn the distinction since 0.5.1, with
the reason in its docstring: a *bare* slash carries meaning inside a statement —
combined issues (`v.1/2`), combined months (`Jul./Aug.`), split years (`1990/91`)
— and splitting on those would corrupt it. Only whitespace on both sides makes it
a separator. None of the conditions the comma test applies are needed: unlike a
comma, a spaced slash is never part of a caption or a designation.

And refusing the end unit only nulled the end, leaving the truncated start. The
message has always promised the whole statement is refused; now it is.

The second half is latent — with the slash read properly, no statement in either
corpus reaches it — and it is fixed anyway, because a message that describes
behaviour the code does not have is a defect whether or not anything triggers it
today. It is tested against `v. 1 (1990)-v. 3 Suppl. (1992)`, where `Suppl.` is
what stops the end unit, so the two changes are held apart.

Exactly one statement in the 141 across both corpora changes, and it changes to
the same correct output on both paths. The corpus report is unmoved: the
statement lives in the synthetic `.mrc`, not the text corpus, which is the third
time in this log that a defect has sat outside what the audit reads.

### D28 — the year-only shorthand keeps its split year raw · **FIXED in 0.9.9**

Noticed while fixing D27, and smaller than it looked.

```
1996/97
  -> 863 40 $8 1.1
  ! '1996/97' is not something a year subfield can hold — it takes MARC codes,
    not wording — so it was left out.
```

An empty 863 from a statement whose only content is a year.

**Cause.** D19 (0.8.1) taught the parser that a year written across the turn of
one is a single publication year — `1996/97` is `1996/1997` — and `_parse_chron()`
normalises at all four of its sites. The *year-only shorthand* in `_parse_unit()`
was missed and returned the raw match. `$i` holds four-digit years, so the value
was then refused and named, from a statement that had nothing else to convert.

The note written for it during D27 said it "reaches the degenerate path". It does
not: `_parse_degenerate()` handles `2016?` and a bare number and would have
refused this outright. The value comes from `_YEAR_ONLY_RE` in `_parse_unit()`.
Checking that before changing anything is what turned a guess into a one-line
fix in the right place.

**Fixed** by calling `normalise_year()` there, which is the whole change. Three
other sites set a year and none of them needed it: the block grammar's year
pattern is `\d{4}|\?` so a split year cannot reach it, the degenerate path's is
`\d{4}` before a `?`, and `_parse_chron()`'s pairing works on values that have
already been through `normalise_year()`.

```
1996/97          -> $i 1996/1997
1999/00          -> $i 1999/2000          the turn of the century too
1990/91-1995/96  -> $i 1990/1991-1995/1996
```

No statement in either corpus is a bare split year, so nothing there changes and
the report is unmoved — the fourth time in this log a defect has sat outside what
the audit reads. It was found by reading the output of a statement that *is* in
the corpus, which is the only reason it surfaced at all.

## Pattern detector

The detector's *correctness* holds up well: every cluster that generates a regex
matches 100% of its own members, and only the one run-on trips
`MAX_PATTERN_TOKENS`. The problems are all about how much work it hands the
cataloguer, and one is about what it shows them.

### D12 — one shape is clustered as many patterns · 45 statements (40%) · **FIXED in 0.6.4**

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

**Fixed in 0.6.4**, and it went further than predicted: **every** spuriously
split shape is now one cluster. 55 clusters became 44, singletons 39 became 31,
and the four families above — 45 statements costing 15 confirmations — now cost
4.

Both halves landed as one change to the tokeniser. `MON` and `SEASON` became a
single `CHRON` kind, and a slash-joined chronology (`Jul/Aug`,
`Winter/Spring`, `Jan/Feb/Mar`) became *one* `CHRON` token rather than three.
That second half matters twice over: it merges the clusters, and it stops the
generated expression carrying `.{1,8}?` where a literal `/` belongs.

The emitted pattern is the general chronology form, not the forms observed, so
a pattern confirmed from months now also reads the season a later record writes
in the same slot. The label says `CHRON` where it used to say `MON` or
`SEASON` — one row instead of several, which is the point.

### D13 — free text is invisible in the pattern label · **FIXED in 0.6.4**

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

**Fixed in 0.6.4**: `_compact_label()` now emits `‹text›` for an UNKNOWN run, so
`v. 58 Suppl. (Sep 2003)` reads `VOL‹text›(CHRONYEAR)` and no longer looks
identical to a clean statement. No two clusters in the corpus share a label now.

### D14 — detector and converter disagree, and the Workbench sits between them · **RESOLVED in 0.7.4**

`8,13,15,17,19,20-(1982-1994)` clusters happily as `#,#,#,#,#,#-(YEAR-YEAR)`,
generates a valid regex, and matches itself 100%. The converter refuses it
outright (D7, correctly).

The detector is answering "what shape is this", the converter "what does it
mean", and those genuinely differ — but the Workbench presents a confirmed
pattern as a thing that converts. A cataloguer who confirms this pattern has
supplied the missing level, so this may be the intended path working as designed;
worth deciding explicitly rather than leaving to inference.

**Decided in 0.7.4, and the finding got worse before it got better.** Re-checked
after the 0.7.0 enumeration rework, the disagreement turned out to be the
symptom rather than the disease. Confirming this pattern the obvious way — six
captured numbers, six enumeration levels, which is exactly what the Level column
invites — produced:

```
853 31 $8 1 $a v. $b no. $c pt. $d ser. $e level 5 $f level 6 $i (year)
863 40 $8 1.1 $a 8 $b 13 $c 15 $d 17 $e 19 $f 20 $i 1982-1994
```

with **no warning and `needs_review` false**. That reads as one issue numbered
six levels deep. The statement means six separate holdings. It is not a silent
*loss* — it is a silent *invention*, which is worse, and 0.7.0 is what made it
reachable: three levels became six, so the wrong reading became expressible.

The honest constraint is that nothing in the tool can tell a genuinely deep
serial from a list once the values are in hand. So the fix does not refuse and
does not drop: `_check_enumeration_depth()` flags any record claiming more than
three enumeration levels, names the alternative reading, and sets a new
`ConversionResult.flagged` — distinct from `needs_review`, which writes nothing
at all. Here the fields are written, because the cataloguer needs to see them to
judge them; what changed is that the record cannot pass unlooked-at.

Three is the threshold on evidence: MARC 21 allows six (`$a`–`$f`), but this
corpus reaches three exactly once across 112 statements, 89 ranges use two, and
none use four. A guard firing on ordinary depth would be noise, and noise is how
a real warning gets missed.

Two things followed. "Needs attention" in the Workbench now means what it says —
it counted only records where *nothing* converted, so a record could need
attention precisely because of what was written and never appear. And converter
warnings are now shown beside the 863 they describe; they were rendered only for
statements that produced no fields, so anything the converter said about a
record it *did* convert never reached the screen at all.

The original framing — should the detector refuse what the converter refuses? —
is answered no. The detector's job is shape, and making it decline would remove
the only route a cataloguer has to ever handling these. The disagreement is
fine; the silence was not.

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
longer counts as a silent loss. At 0.6.2 that alone took the clean rate from
60% to 71%, with six statements moving from "lost" to "warned" — 0.6.3 then
moved it again by turning ten half-conversions into refusals, so the headline
table is the current figure and this is the step. If that warning were ever
removed, the report would count those statements as losses again.

### Both remaining shapes closed in 0.6.3

The two cases left open above were decided the same way, on the cataloguer's
reading: *if it cannot be told whether a value belongs to the start or the end,
drop it.* A MARC reader pairs the subfields positionally, so a lone value is
read as covering both ends whatever was meant by it.

- **The start-only mirror.** `v. 118 no. 1 (Spring 2012)-v. 122 no. 1 (2016)`
  now writes `$i 2012-2016` with no `$j`, and names the Spring it dropped. The
  rule is symmetric: it no longer matters which end holds the lone value.
- **`(1981 - Sep 1996)`** now drops its month in `_parse_chron`, where the two
  halves of the group are still visible. Same for `Aug 1984-1985`,
  `Feb 1921-1929`, `1981-October 1997` and `1974-Apr 1979`.

Two guards keep this from over-firing, and both were found by a test going red:

- **Nothing above it ranges → keep.** `1983: 5 (7-30 [Jan 28-Dec 29])` states its
  year once, and the block grammar's end boundary carries only a closing month,
  so every block statement looks one-sided. But nothing above the year ranges,
  so `$i 1983 $j 01-12` is unambiguous. An earlier draft dropped the year here.
- **A lone value can already be a pair.** `(Jan 1956 - Jan 1957)` reaches the
  converter as a single `01-01` hanging off the end boundary, which looks
  one-sided and is not. What separates it from the `1-2` of
  `v. 1 (1956) - v. 51 nos. 1-2 (2006)` — a range *inside* one boundary — is
  whether the other boundary states anything at all at that level.

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
3. ~~**D1 and D3 together**~~ Done in 0.6.3. Ten silent losses became visible
   refusals; *parsing* a discontinuous list or a supplement designation is the
   larger, separate job and is still open.
4. ~~**D2**~~ Done in 0.6.2. The fix was not the fallback proposed above —
   see D2's section for why that would have been wrong.
5. ~~**D5**~~ Done in 0.6.4, with D4 and D9.
6. ~~**D12/D13**~~ Done in 0.6.4 — and the merge removed *all* spurious
   splitting, not just the four families measured.
7. ~~**D6**~~ Done in 0.7.0, as the enumeration-hierarchy rework it needed — not
   as a patch. D8 fell out of the same change. See D6's section.

### What is left

- **D7** — genuinely captionless statements. Expected to keep failing; the
  Workbench's confirm step is the mechanism that could convert them.
- **D11** — by design, and documented as such.
- ~~**D20**~~ Fixed in 0.8.2 — the guard measures the generated expression
  rather than estimating from the token count, so the invariant holds by
  construction.
- ~~**D10**~~ Half fixed in 0.8.0. The detector's complexity guard still
  declines the run-on, which is intended; the silent day loss inside it is
  gone.
- ~~**D14**~~ Resolved in 0.7.4 — and it was a bug after all, just not the one
  logged: a record claiming implausible enumeration depth is now flagged rather
  than passing as ordinary. See D14's section.

The bounded-error work cuts across all of these and is worth doing alongside
rather than after: every fix on this list was easier to trust because the report
could show what it changed.

## One reader, not two · **DONE in 0.10.0**

*16 September 2026.*

The finding that runs underneath most of the others, written up once the
evidence for it was measurable.

**What was wrong.** A statement could reach MARC by two independent routes. A
confirmed pattern built a `ParseResult` itself, out of what its capture groups
caught; `parse_866()` built one by reading the text. Two implementations of the
same job, and whichever ran was whichever the caller happened to pick.

**How much of this log is that finding.** D24, D25, D26 and its regression,
0.8.6, 0.9.3 and the run-index investigation were all, in the end, "the two
paths disagree about this statement", and the resolution was almost always
"make the pattern path do what the parser does". Each was fixed on its own
terms. None of them removed the reason there was something to fix.

**Why it kept getting missed.** `corpus_report.py --drift` reported *no drift*
for D26, its regression, D27 and D28 — because it only ever runs the parser.
The "0 silent losses" figure quoted throughout this document never covered the
pattern path at all.
`test_the_pattern_path_never_drops_a_chronology_the_parser_keeps` was added to
close that blind spot, and it did, but a test asserting that two
implementations agree is a design paying rent on the second one.

**The measurement.** Both paths were run over all 141 statements in the corpus
and the two `.mrc` fixtures, and the converted fields compared:

| | |
|---|---|
| identical | 90 |
| only the parser writes anything | 12 |
| only the pattern writes anything | 5 |
| **they disagree** | **10** |

Of the 10 disagreements the pattern was wrong in 9. Eight declared a `$j`
caption in the 853 that their own 863 never filled; one lost `[Sum]`; one
produced nothing where the parser converted correctly. The tenth,
`v. 15 (1998 Buyers Guide)`, is a parser defect (D5) the pattern happened to
sidestep. Of the 5 the pattern alone converted, 3 were supplements belonging in
an 867, and one produced no fields at all.

So the second implementation's only wins were statements it converted into the
wrong field.

**What was changed.** `parse_866()` is the single reader of holdings structure.
`build_parse_result()` still answers *whether the pattern applies* — that is the
regex's question, and every caller depends on it, since `fallback=False` must
write nothing for an unmatched statement and a skip pattern must claim only what
it matches — and then hands the reading to the parser. `_build_from_pattern()`
keeps the old construction for the one case that needs it: a statement the
parser writes nothing for, where the cataloguer's confirmation is the only thing
that can settle what a value means. `v.1(1990)-5(1994)` is the example — a `5`
no caption reaches, which no amount of parsing can type.

Captions are the other thing a pattern still decides. Where a level is written
as a bare number the parser writes `NO_CAPTION` and the 853 declares `(*)`; the
confirmed word is filled in there. Only there: a caption the statement states is
what the piece in hand says, and a pattern's generic `v.` must not overwrite a
`vol.` printed on the volume.

**What it cost and what it bought.** Nothing on the parser path changed — all
141 statements convert exactly as before. On the pattern path, nothing was lost,
22 outputs changed and every one is an improvement: five statements that
produced nothing now convert in full, `2018: ([Sum])` keeps its season,
`v.7/8(1996:Jul./Aug.)` keeps both months, and eight stop declaring a caption
they never fill. Two entries from the backlog closed without being worked on.

Where both paths now write an 863 they write the same 863, on 117 of 117
statements. `test_the_two_paths_write_the_same_863()` asserts exactly that, and
`KNOWN_CHRON_GAPS` is empty for the first time.

**What is left.** The three Flask applications still duplicate their MARC glue
and carry a session each. That is the next structural piece, and it is
independent of this one.

---

## The screen catches up with the parser · **DONE in 0.11.1**

*16 September 2026. The interface half of "One reader, not two".*

0.10.0 made the parser the single reader of holdings structure, leaving a
confirmed pattern to supply only what the parser cannot settle. The Patterns
step still asked about every pattern in the same words.

**Measured over the corpus.** Of 44 clusters the screen asked about 6:

| What the cataloguer's answer would do | Clusters |
|---|---|
| decide the reading — the parser writes nothing | 1 |
| supply a caption — the parser reads it, but writes `(*)` | 4 |
| **change nothing** — the parser reads it, captions and all | **1** |

The last row is the defect in miniature: `Series 1, v. 6 no. 1 (Summer/Fall
1992)` captures a bare `1` the detector cannot type, so a role came back
unresolved and the screen asked. The parser reads the statement as `ser. 1`,
`v. 6`, `no. 1` without help, so whatever was answered was discarded.

**What was changed.** `_what_confirming_decides()` parses a cluster's statements
and returns `reading`, `caption` or `nothing`. Each card carries a note saying
which, and `needs_decision` is now "a decision is outstanding *and* the answer
would change something". A pattern that changes nothing is folded away with the
confirmed ones rather than sitting in the work queue.

**What the measurement turned up on the way.** Four clusters decide the
*reading*, but only one reached the screen — the other three were
auto-confirmed, because auto-confirmation asks only whether every role is
resolved. They were:

    v. 19 no. 2 Suppl. (1998)
    v. 58 Suppl. (Sep 2003)
    Special Issue (October/November 1995)

These are the statements the audit for "One reader, not two" found converting
into the *wrong field* — supplements belong in an 867, not an 863. The parser
refuses them deliberately: it reads `v. 19 no. 2` and cannot account for
`Suppl.`, so it writes nothing. An auto-confirmed pattern then overrode that
refusal and wrote an 863 saying the library holds volume 19 number 2, when what
it holds is a supplement to it — and no cataloguer ever saw the decision.

So auto-confirmation now skips any pattern that decides a reading, however
confident its roles look. The rule is general, not about supplements: a pattern
overriding the parser's deliberate refusal is exactly the case a human should
see. Flagging `Suppl.` as belonging in an 867 remains a separate, unstarted
feature — this only stops the statements being converted silently in the
meantime.

The cost is more questions: across the corpus the screen asks about 9 rather
than 6, and every one it adds is a supplement, an unnumbered special issue, a
bare number no caption reaches, or the brace-note statement. No false positives.

---

## Requested, not yet started

Raised 1 September 2026 alongside D15–D18, recorded here so they are not lost.
The first two are done; the rest are Workbench UI and are not started.

- ~~**Enumeration levels are positional, not named.**~~ Done in 0.7.0, with D6.
  `caption_slot()` used to map `no.` to `issue` and `_build_853()` put issue in
  `$b`, so the level a caption occupied was hard-coded to the word used. It is
  now an ordered list of any depth: `caption_slot()` answers only "enumeration,
  year or month", the subfield comes from position, and the caption is whatever
  word the statement used. See D6.
- ~~**Skip a pattern or a record.**~~ Done in 0.7.2. A skipped pattern still
  has to *match* — that is what stops its statements falling through to the
  standard parser and being converted anyway — and skipping deliberately does
  not require the values to be decided first, since not knowing what a shape
  means is a good reason to leave it alone. A skipped record is not touched at
  all, including by "clear existing 853/863".
- ~~**Split on top-level commas, semicolons or slashes should default to
  OFF.**~~ Done in 0.6.4.
- ~~**The pattern library needs to collapse.**~~ Done in 0.7.1. Two folds, not
  one: patterns needing nothing from the cataloguer (confirmed, or too
  idiosyncratic to express) now sit in a collapsed group, and the whole
  Patterns step folds to a line carrying its own counts. On the corpus, 37 of
  44 cards fold away and the step goes from ~4,600px to ~1,300px, or ~124px
  folded. The folded line names what still needs a decision, so folding never
  hides work.
- ~~**Jump from a record in Convert back to its pattern**~~ Done in 0.7.3. The
  link unfolds step 2, opens the group the pattern sits in, and opens the
  pattern itself. The re-review half needed something the screen did not have:
  a per-record record of *which pattern read it*, for the whole file rather
  than the loaded page. Adding that also fixed the filter bug below.
- ~~**The Convert filters only saw the loaded page.**~~ Fixed in 0.7.3. Found
  by the cataloguer, not by the corpus: previews were fetched fifty at a time,
  and `recordMatchesFilter` returned `true` for a record it had no data for —
  so every record past the first page passed every filter. On a 120-record file
  "needs attention" showed 86 records where 8 matched. The counts now come from
  `/api/review-index`, which answers for every record, and paging walks what
  the filter shows rather than the file in order.
- **Flag a statement that belongs in another field.** Raised 15 September 2026,
  not started. An 866 saying `Suppl.` is describing supplementary material,
  which MARC 21 puts in **867** with its own **864** enumeration; one saying
  `Index` belongs in **868** over **865**. The toolkit has no notion of this: it
  reads every 866 as basic bibliographic holdings, and the three `Suppl.`
  statements in the corpus are the only reason D3 still has anything in it.

  Converting them into 867/868 is a bigger question — it changes which field the
  output goes to, and the cataloguer may want to move the *source* statement
  too. Flagging is the cheap, useful half: say that this statement looks like
  supplementary material or an index and does not belong in an 866, and leave
  the move to a person. Worth noting that the pattern path already *converts*
  these, into an 863, which is the wrong field — so the flag is also a guard.

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
