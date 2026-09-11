# Corpus findings — what `textual_holdings_corpus.txt` reveals

Written 1 September 2026, after adopting the hand-collected 866 `$a` corpus that
predates the toolkit — the examples the original monolithic regex was written
against — as `data/textual_holdings_corpus.txt`.

This is a log of what the corpus exposes, so that fixing any of it is a
deliberate, separately reviewable decision. Everything here was found before
anything was changed.

**Fixed so far:** D17 and D18 (0.6.1); D2, D15 and D16 (0.6.2); D1 and D3
(0.6.3); D4, D5, D9, D12 and D13 (0.6.4); D6 and D8 (0.7.0); D14 (0.7.4);
D10 (0.8.0); D19 (0.8.1); D20 (0.8.2); D21 (0.8.4, 11 September 2026).
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

| | at 0.6.0 | now (0.8.1) |
|---|---|---|
| statements | 117 unique (132 before de-duplication), 11 sections | — |
| converted cleanly | 67 (60%) | **83 (71%)** |
| converted with values **silently** dropped | 36 (32%) | **0** |
| converted, and told the cataloguer what it dropped | 3 | **21** |
| produced no fields at all | 6 (5%) | **13 (12%)** |
| detector clusters | 55, 39 of them singletons | **44, 31 singletons** |
| one shape split across several clusters | 45 statements, 15 confirmations | **0** |
| statements a pattern could claim only part of | 37 (33%) | 64, none convert |

The silent-loss column is the one to watch, and the clean rate is not. Statements
have moved *out* of "clean" in both directions on purpose: ten now refuse
outright rather than convert a third of themselves (D1, D3), and twenty more
convert while naming a value they could not place. Both are the same trade —
less written, and what is written is true. The clean rate rose again in 0.7.0
for a different reason: four statements the model simply could not express now
convert whole (D6, D8).

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

### D1 — a discontinuous list is truncated at its first comma · 7 statements · **FIXED in 0.6.3**

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

### D7 — genuinely captionless statements · 3 statements · expected fail

```
8,13,15,17,19,20-(1982-1994)
50th Anniversary Issue (2017)
Special Issue (October/November 1995)
```

These the monolith never handled either, and they should stay failing. Nothing in
`8,13,15,...` says whether those are volumes, issues or years, and refusing is
the documented, correct behaviour — the same argument the README makes about
`?: 16` under "How the Workbench joins the two tools". A cataloguer supplies the
level; the parser cannot.

`Special Issue` and `50th Anniversary Issue` are worth one note: the Workbench's
confirm step is exactly the mechanism that could convert these, since a human
says once what the captured values mean.

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
characters — and what would end it is a match timeout, which the tool does not
have at any cap value. That is worth doing and is not done here.

So the cap is what it always really was: the point past which an expression is
too unwieldy to read, edit or test. 4,000 admits everything the corpus produces
(worst: 2,384) with headroom for the per-token cost to grow again the way D19
grew it. The length guard is now a backstop behind `MAX_PATTERN_TOKENS` rather
than the binding constraint, which is the right relationship: the token count
decides "too idiosyncratic", and the measured length guarantees the invariant
whatever future change alters a token's cost.

One corpus cluster came back as a result: `v. 19 nos. 1, 3, 5, 7-12 (Jan, Mar,
May, Jul-Dec 1915)`, which now generates a usable pattern. The converter still
refuses the statement as a discontinuous list (D1), so the pattern path is the
only route it has.

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
