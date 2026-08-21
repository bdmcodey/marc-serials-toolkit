"""
Structural properties that must hold for any input, on any corpus.

These are the portable half of the verification table in HANDOFF.md. The exact
counts recorded there describe two specific private files and live in
test_calibration.py; the properties here describe the tool itself and are
checked against every corpus this machine can reach. On a clean clone that is
the two committed synthetic files; with MARC_TEST_DATA_DIR pointing at the
mounted share it is those plus the real ones, with no change to the tests.

Each test takes `any_corpus` and is therefore run once per corpus.
"""

from __future__ import annotations

import io

import pytest
from pymarc import MARCReader

from conftest import upload_marc
from pattern_detector import detect_patterns, MAX_PATTERN_TOKENS


# ---------------------------------------------------------------------------
# Helpers -- each returns the offending values so a failure reads as data
# ---------------------------------------------------------------------------

def _records(data: bytes) -> list:
    return [r for r in MARCReader(io.BytesIO(data)) if r is not None]


def _link_of(field) -> str:
    """The $8 value of a field, or "" when it carries none."""
    return field["8"] or ""


def duplicate_853_links(record) -> list[str]:
    """Linking numbers claimed by more than one 853 on the same record."""
    links = [_link_of(f) for f in record.get_fields("853")]
    return sorted({link for link in links if links.count(link) > 1})


def orphaned_863s(record) -> list[str]:
    """
    863 links whose parent 853 is absent.

    An 853 with no $8 at all is excluded from the parent set deliberately: the
    converter falls back to a bare linking number in that case, and treating it
    as a parent would hide a real orphan behind an unrelated field.
    """
    parents = {_link_of(f) for f in record.get_fields("853") if _link_of(f)}
    if not parents:
        return []
    orphans = []
    for field in record.get_fields("863"):
        prefix = _link_of(field).split(".")[0]
        if prefix and prefix not in parents:
            orphans.append(_link_of(field))
    return orphans


def malformed_links(record) -> list[str]:
    """853 $8 must be a bare integer; 863 $8 must be int.int."""
    bad = []
    for field in record.get_fields("853"):
        link = _link_of(field)
        if link and not link.isdigit():
            bad.append(f"853 ${{8}} {link!r}")
    for field in record.get_fields("863"):
        link = _link_of(field)
        if not link:
            continue
        parts = link.split(".")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            bad.append(f"863 ${{8}} {link!r}")
    return bad


def _convert(client, data: bytes) -> bytes:
    upload_marc(client, data)
    client.post("/api/batch-convert", json={"convention": "standard"})
    return client.get("/api/download-converted").data


# ---------------------------------------------------------------------------
# Converter invariants
# ---------------------------------------------------------------------------

def test_output_is_readable_marc(converter_client, any_corpus):
    """Whatever else happens, the file has to come back out as MARC."""
    before = _records(any_corpus)
    after = _records(_convert(converter_client, any_corpus))
    assert len(after) == len(before)


def test_no_duplicate_853_links(converter_client, any_corpus):
    """
    Two 853s sharing a linking number make the record ambiguous: an 863 pointing
    at that number no longer names one pattern.
    """
    for index, record in enumerate(_records(_convert(converter_client, any_corpus))):
        assert duplicate_853_links(record) == [], f"record {index}"


def test_no_orphaned_863s(converter_client, any_corpus):
    """An 863 whose 853 is missing describes a pattern that is not there."""
    for index, record in enumerate(_records(_convert(converter_client, any_corpus))):
        assert orphaned_863s(record) == [], f"record {index}"


def test_links_are_well_formed(converter_client, any_corpus):
    for index, record in enumerate(_records(_convert(converter_client, any_corpus))):
        assert malformed_links(record) == [], f"record {index}"


def test_863_sequence_is_contiguous(converter_client, any_corpus):
    """
    Within one link the 863s run 1..n. A gap or a repeat means a statement was
    dropped or counted twice while numbering was assigned across the record.
    """
    for index, record in enumerate(_records(_convert(converter_client, any_corpus))):
        by_parent: dict[str, list[int]] = {}
        for field in record.get_fields("863"):
            parent, _, seq = _link_of(field).partition(".")
            if seq.isdigit():
                by_parent.setdefault(parent, []).append(int(seq))
        for parent, seqs in by_parent.items():
            assert sorted(seqs) == list(range(1, len(seqs) + 1)), \
                f"record {index}, link {parent}: {sorted(seqs)}"


def test_conversion_is_idempotent(converter_client, any_corpus):
    """
    Converting an already-converted file must not stack a second set of 863s on
    top of the first. This is the property that makes re-running safe.
    """
    once = _convert(converter_client, any_corpus)
    twice = _convert(converter_client, once)
    assert twice == once


def _holdings_survived(converter_client, corpus: bytes) -> list[int]:
    """
    Indexes of records that arrived carrying an 866 and left with nothing.

    Surviving means holdings in some form: converted into 853/863, or left as
    the original 866. None of the three means the tool destroyed data it could
    not understand.
    """
    before = _records(corpus)
    after = _records(_convert(converter_client, corpus))

    lost = []
    for index, (original, converted) in enumerate(zip(before, after)):
        if not original.get_fields("866"):
            continue
        surviving = (converted.get_fields("853")
                     + converted.get_fields("863")
                     + converted.get_fields("866"))
        if not surviving:
            lost.append(index)
    return lost


@pytest.mark.xfail(reason="api_batch_convert strips the source 866 whenever "
                          "needs_review is 0, which a hard parse failure also "
                          "satisfies -- see the strict regression test below")
def test_no_record_loses_its_only_holdings(converter_client, any_corpus):
    """
    Not strict, because whether a corpus trips the defect depends on whether it
    happens to contain a statement neither grammar accepts. The example corpus
    does and the messy one does not, so a strict marker here would fail on the
    corpus where the invariant legitimately holds. The deterministic case is
    pinned strictly in test_unparseable_statement_is_never_deleted().
    """
    assert _holdings_survived(converter_client, any_corpus) == []


@pytest.mark.xfail(strict=True,
                   reason="a statement that fails to parse sets success=False "
                          "but leaves needs_review at 0, so the `review == 0` "
                          "guard strips its 866 with nothing to replace it")
def test_unparseable_statement_is_never_deleted(converter_client, example_marc_bytes):
    """
    Record 3 of data/example_holdings.mrc carries two statements that neither
    grammar accepts. It goes in with two 866s and comes out with no 866, no 853
    and no 863 -- the holdings are gone -- while the API reports success and the
    per-record summary shows converted_fields: 0.

    The guard in api_batch_convert reads

        if remove_866 and review == 0:

    and its comment says a statement held back for review keeps its original
    data. But needs_review is only set when values were found and could not be
    placed; a hard parse failure leaves it at 0, so the 866 is stripped exactly
    as if it had been converted.

    Strict, because this case is deterministic: fixing the guard must turn this
    green, and a fix that only half-works must still fail.
    """
    assert _holdings_survived(converter_client, example_marc_bytes) == []


# ---------------------------------------------------------------------------
# Detector invariants
# ---------------------------------------------------------------------------

def _statements(detector_client, corpus: bytes) -> list[str]:
    return upload_marc(detector_client, corpus).get_json()["statements"]


def test_every_generated_regex_matches_its_own_cluster(detector_client, any_corpus):
    """
    The detector's central claim. A regex that does not match the statements it
    was generated from is worse than no regex, because it looks authoritative.
    """
    for group in detect_patterns(_statements(detector_client, any_corpus)):
        if group.too_complex:
            continue
        assert group.match_rate == 1.0, group.human_label
        assert group.failed == []


def test_every_generated_regex_is_testable(detector_client, any_corpus):
    """
    2,000 characters is the ceiling /api/test-regex enforces. Emitting a longer
    one would mean the tool rejecting its own output.
    """
    for group in detect_patterns(_statements(detector_client, any_corpus)):
        assert len(group.regex) <= 2000, group.human_label


def test_complexity_guard_and_output_agree(detector_client, any_corpus):
    for group in detect_patterns(_statements(detector_client, any_corpus)):
        assert group.too_complex == (group.token_count > MAX_PATTERN_TOKENS)
        assert group.too_complex == (group.regex == "")


def test_no_statement_is_lost_in_clustering(detector_client, any_corpus):
    statements = _statements(detector_client, any_corpus)
    groups = detect_patterns(statements)
    assert sum(g.count for g in groups) == len([s for s in statements if s.strip()])
