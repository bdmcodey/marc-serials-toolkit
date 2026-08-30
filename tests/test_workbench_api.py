"""
The workbench's HTTP surface, and the promise it makes about the converter.

The test that matters most is test_empty_library_matches_the_converter_exactly:
merging the two tools is only worth doing if the converter's output is
untouched when no pattern has been confirmed. It is asserted byte for byte,
against every corpus this machine can reach.
"""

from __future__ import annotations

import json
import io

import pytest

from conftest import upload_marc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# One settings body for both apps: their conversion endpoints read the same
# keys, which is what makes the equivalence comparison meaningful.
SETTINGS = {
    "convention": "standard",
    "frequency": "q",
    "numbering_continuity": "r",
    "remove_866": True,
    "clear_existing_853_863": False,
}


def detect(client, statements, split=True):
    response = client.post("/api/detect", json={
        "statements": statements, "split_multi_range": split})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def group_for(client, statement, split=True):
    """The single detected group for one statement."""
    body = detect(client, [statement], split)
    assert body["total_patterns"] == 1
    return body["groups"][0]


def confirm(client, group, decisions=None, split=True, priority=None):
    """
    Confirm a pattern the way the screen does: take the suggested roles, apply
    the cataloguer's corrections, and PUT the library.
    """
    roles = [dict(r) for r in group["suggested_roles"]]
    for role in roles:
        if decisions and role["group"] in decisions:
            boundary, level = decisions[role["group"]]
            role["boundary"], role["level"] = boundary, level

    entry = {
        "id": "confirmed-1",
        "label": group["human_label"],
        "regex": group["regex"],
        "roles": roles,
        "split": split,
        "priority": group["count"] if priority is None else priority,
    }
    response = client.put("/api/patterns", json={"patterns": [entry]})
    assert response.status_code == 200
    return response.get_json()


def record_index_with(records, statement):
    """The index of the record carrying `statement` in an 866."""
    for rec in records:
        if any((f["a"] or "").strip() == statement for f in rec["fields_866"]):
            return rec["index"]
    pytest.fail(f"no record in this corpus carries {statement!r}")


def previews_for(client, index):
    response = client.post("/api/preview-record", json={
        "record_index": index, **SETTINGS})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["previews"]


# ---------------------------------------------------------------------------
# One upload, both halves
# ---------------------------------------------------------------------------

def test_upload_serves_the_record_list_and_the_statements(workbench_client,
                                                          example_marc_bytes):
    """
    The integration win, at its plainest: the file is read once and both sides
    of the tool are fed from it.
    """
    body = upload_marc(workbench_client, example_marc_bytes).get_json()
    assert body["total"] == 5
    assert body["count"] == 9
    assert "v.6(1995)-" in body["statements"]
    assert body["records"][0]["fields_866"]


def test_detection_can_read_the_uploaded_file_without_being_resent_it(
        workbench_client, example_marc_bytes):
    upload_marc(workbench_client, example_marc_bytes)
    body = workbench_client.post("/api/detect", json={}).get_json()
    assert body["total_statements"] > 0
    assert body["total_patterns"] > 0


def test_detection_without_statements_or_a_file_is_refused(workbench_client):
    assert workbench_client.post("/api/detect", json={}).status_code == 400


# ---------------------------------------------------------------------------
# The confirmation screen
# ---------------------------------------------------------------------------

def test_every_group_arrives_with_roles_and_the_values_they_would_take(
        workbench_client):
    group = group_for(workbench_client, "v.1(1990)-v.5(1994)")
    roles = {r["group"]: r for r in group["suggested_roles"]}
    assert set(roles) == set(group["named_groups"])
    assert roles["start_vol"]["level"] == "vol"
    assert roles["start_vol"]["level_label"]          # shown to a cataloguer
    assert group["example_values"][0]["start_year"] == "1990"
    assert group["example_values"][0]["end_year"] == "1994"


def test_values_are_reported_per_example_not_pooled(workbench_client):
    """
    A cataloguer checks one statement at a time. Pooling values across examples
    produced a column that could not be lined up against any single 866.
    """
    body = detect(workbench_client, ["v.1(1990)-v.5(1994)", "v.2(1991)-v.6(1995)"])
    group = body["groups"][0]
    assert group["count"] == 2
    assert [v["start_vol"] for v in group["example_values"]] == ["1", "2"]
    assert [v["end_year"] for v in group["example_values"]] == ["1994", "1995"]


def test_an_example_resolves_to_the_record_it_came_from(workbench_client,
                                                        messy_marc_bytes):
    """
    Splitting means an example need not equal any $a: "v.1(1990)-v.3(1992) /
    v.5(1994)-v.8(1997)" becomes two statements, neither of which the client
    could match back to a record by comparing text. The association is made on
    the server while the statements are being taken apart.
    """
    records = upload_marc(workbench_client, messy_marc_bytes).get_json()["records"]
    expected = record_index_with(records, "v.1(1990)-v.3(1992) / v.5(1994)-v.8(1997)")

    groups = workbench_client.post("/api/detect", json={}).get_json()["groups"]
    found = [
        (g["examples"][i], src)
        for g in groups
        for i, src in enumerate(g.get("example_sources") or [])
        if src and g["examples"][i] == "v.5(1994)-v.8(1997)"
    ]
    assert found, "the split sub-statement resolved to no record"
    statement, source = found[0]
    assert source["record_index"] == expected
    assert source["source_866"] == "v.1(1990)-v.3(1992) / v.5(1994)-v.8(1997)"


def test_a_group_needing_a_decision_says_so(workbench_client):
    """
    A captionless value the detector cannot place has to be flagged, so the card
    shows the amber pill rather than looking ready to confirm.
    """
    group = group_for(workbench_client, "v.1(1990)-5(1994)", split=False)
    assert group["needs_decision"] is True


def test_a_compressed_range_needs_no_decision_at_all(workbench_client):
    """
    "v.1-5(1990-1994)" used to need one: the detector named both years end_year
    and left the 5 as a captionless number. It now reads all four values, so the
    cataloguer confirms it without having to correct anything first.
    """
    group = group_for(workbench_client, "v.1-5(1990-1994)", split=False)
    assert group["needs_decision"] is False
    assert group["named_groups"] == ["start_vol", "end_vol", "start_year", "end_year"]


def test_preview_shows_the_pattern_beside_the_parser(workbench_client):
    group = group_for(workbench_client, "v.1(1990)-v.5(1994)")
    body = workbench_client.post("/api/pattern-preview", json={
        "regex": group["regex"],
        "roles": group["suggested_roles"],
        "statements": group["examples"],
        "split": True,
        **SETTINGS,
    }).get_json()

    preview = body["previews"][0]
    assert preview["matched"] is True
    assert preview["pattern"]["field_853"].startswith("853")
    assert preview["parser"]["field_853"].startswith("853")
    assert preview["differs"] is False        # the parser already reads this one


def test_correcting_a_role_changes_the_marc_the_screen_shows(workbench_client):
    """
    The cataloguer's loop, end to end over HTTP: a real separator divides
    "v.1(1990)-5(1994)", so the "v." does not reach the 5 and nothing is encoded
    for it; saying it is the end volume puts it in the 863.
    """
    statement = "v.1(1990)-5(1994)"
    group = group_for(workbench_client, statement, split=False)

    def preview(roles):
        return workbench_client.post("/api/pattern-preview", json={
            "regex": group["regex"], "roles": roles,
            "statements": [statement], "split": False, **SETTINGS,
        }).get_json()["previews"][0]

    before = workbench_client.post("/api/pattern-preview", json={
        "regex": group["regex"], "roles": group["suggested_roles"],
        "statements": [statement], "split": False, **SETTINGS,
    }).get_json()
    # Nothing is previewed while a value has no meaning: there is no honest
    # answer to show, and showing a partial one invites confirming it.
    assert before["scope"] == "unresolved"
    assert before["unresolved"] == ["start_num"]

    corrected = [dict(r) for r in group["suggested_roles"]]
    for role in corrected:
        if role["group"] == "start_num":
            role["boundary"], role["level"] = "end", "vol"

    after = preview(corrected)
    assert "$a 1-5" in after["pattern"]["fields_863"][0]


def test_preview_reports_a_statement_the_pattern_cannot_read(workbench_client):
    group = group_for(workbench_client, "v.1(1990)-v.5(1994)")
    body = workbench_client.post("/api/pattern-preview", json={
        "regex": group["regex"], "roles": group["suggested_roles"],
        "statements": ["1993: (1 [Feb])"], **SETTINGS,
    }).get_json()
    assert body["previews"][0]["matched"] is False
    assert body["previews"][0]["pattern"] is None
    assert body["previews"][0]["parser"]["fields_863"]     # the parser still reads it


def test_editing_the_expression_returns_roles_for_it(workbench_client):
    group = group_for(workbench_client, "v.1(1990)-v.5(1994)")
    body = workbench_client.post("/api/test-regex", json={
        "regex": group["regex"], "statements": group["examples"],
    }).get_json()
    assert body["match_rate"] == 1.0
    assert [r["group"] for r in body["roles"]] == group["named_groups"]


def test_an_unreadable_expression_is_reported_not_raised(workbench_client):
    response = workbench_client.post("/api/test-regex", json={
        "regex": "(?P<start_vol>[", "statements": ["v.1(1990)"]})
    assert response.status_code == 400
    assert "Invalid regex" in response.get_json()["error"]


def test_preview_numbers_a_statement_as_its_record_would(workbench_client,
                                                         example_marc_bytes):
    """
    $8 is a record-level decision, so a statement previewed on its own always
    read "1.1" whatever it would really receive. The confirmation screen exists
    to show a cataloguer the output they are approving; showing numbering that
    conversion will not write defeats it.

    The preview must therefore agree with /api/preview-record, which is what
    conversion actually goes through.
    """
    records = upload_marc(workbench_client, example_marc_bytes).get_json()["records"]
    idx = record_index_with(records, "v.1(1990)-v.10(1999)")
    group = group_for(workbench_client, "v.1(1990)-v.10(1999)")

    body = workbench_client.post("/api/pattern-preview", json={
        "regex": group["regex"], "roles": group["suggested_roles"],
        "record_index": idx, "field_index": 0, "split": True, **SETTINGS,
    }).get_json()

    assert body["scope"] == "record"
    assert body["record"]["index"] == idx
    assert body["record"]["title"]

    from_record = previews_for(workbench_client, idx)
    assert [p["fields_863"] for p in body["previews"]] == \
           [p["fields_863"] for p in from_record]


def test_preview_marks_the_example_among_its_siblings(workbench_client,
                                                      example_marc_bytes):
    records = upload_marc(workbench_client, example_marc_bytes).get_json()["records"]
    idx = record_index_with(records, "v.12(2001)-v.15(2004)")
    field_index = next(i for i, f in enumerate(records[idx]["fields_866"])
                       if f["a"] == "v.12(2001)-v.15(2004)")
    group = group_for(workbench_client, "v.12(2001)-v.15(2004)")

    body = workbench_client.post("/api/pattern-preview", json={
        "regex": group["regex"], "roles": group["suggested_roles"],
        "record_index": idx, "field_index": field_index, "split": True, **SETTINGS,
    }).get_json()

    marked = [p for p in body["previews"] if p["is_example"]]
    assert len(marked) == 1
    assert marked[0]["source_866"] == "v.12(2001)-v.15(2004)"


def preview_links(client, statement, records, split=True):
    """The $8 values a record's 863s get when previewing one of its patterns."""
    idx = record_index_with(records, statement)
    group = group_for(client, statement, split=split)
    body = client.post("/api/pattern-preview", json={
        "regex": group["regex"], "roles": group["suggested_roles"],
        "record_index": idx, "field_index": 0, "split": split, **SETTINGS,
    }).get_json()
    return [f.split("$8")[1].split()[0]
            for p in body["previews"] for f in p["fields_863"]]


def test_statements_sharing_a_pattern_share_an_853(workbench_client,
                                                   example_marc_bytes):
    """
    Half of the behaviour the change exists for: two statements of the same
    publication pattern sit under one 853 and run 1.1, 1.2 -- not 1.1 twice,
    which is what previewing each statement in isolation used to show.
    """
    records = upload_marc(workbench_client, example_marc_bytes).get_json()["records"]
    assert preview_links(workbench_client, "v.1(1990)-v.10(1999)", records) == \
        ["1.1", "1.2"]


def test_a_different_pattern_starts_a_new_linking_number(workbench_client,
                                                         messy_marc_bytes):
    """
    The other half: when the publication pattern actually changes, numbering
    moves to 2.1 rather than continuing.

    This record shows both halves at once. Its first 866 holds two slash-
    separated ranges, which the pattern splits into 1.1 and 1.2 -- the standard
    parser drops the second outright (see the xfail
    test_slash_separated_ranges_should_both_survive), so a confirmed pattern
    recovers holdings here as well as numbering them. Its second 866 is a
    different shape, and starts 2.1.
    """
    records = upload_marc(workbench_client, messy_marc_bytes).get_json()["records"]
    assert preview_links(
        workbench_client, "v.1(1990)-v.3(1992) / v.5(1994)-v.8(1997)", records
    ) == ["1.1", "1.2", "2.1"]


def test_the_candidate_pattern_outranks_the_library_but_is_never_stored(
        workbench_client, example_marc_bytes):
    """
    The cataloguer is looking at this pattern, so it must be the one that reads
    its own shape -- but previewing must not quietly add it to the library.
    """
    records = upload_marc(workbench_client, example_marc_bytes).get_json()["records"]
    idx = record_index_with(records, "v.6(1995)-")
    group = group_for(workbench_client, "v.6(1995)-")

    body = workbench_client.post("/api/pattern-preview", json={
        "regex": group["regex"], "roles": group["suggested_roles"],
        "record_index": idx, "field_index": 0, "split": True, **SETTINGS,
    }).get_json()

    used = [p["source_label"] for p in body["previews"] if p["source_866"] == "v.6(1995)-"]
    assert used == ["This pattern"]
    assert workbench_client.get("/api/patterns").get_json()["count"] == 0


def test_a_statement_with_no_record_still_previews(workbench_client):
    """Pasted statements have no record to sit in; they fall back gracefully."""
    group = group_for(workbench_client, "v.1(1990)-v.5(1994)")
    body = workbench_client.post("/api/pattern-preview", json={
        "regex": group["regex"], "roles": group["suggested_roles"],
        "statements": ["v.1(1990)-v.5(1994)"], "split": True, **SETTINGS,
    }).get_json()
    assert body["scope"] == "statement"
    assert body["previews"][0]["pattern"]["fields_863"]
    assert body["previews"][0]["parser"]["fields_863"]


# ---------------------------------------------------------------------------
# The library
# ---------------------------------------------------------------------------

def test_confirming_a_pattern_stores_it(workbench_client):
    group = group_for(workbench_client, "v.1(1990)-v.5(1994)")
    body = confirm(workbench_client, group)
    assert body["count"] == 1
    assert not body["rejected"]
    assert workbench_client.get("/api/patterns").get_json()["count"] == 1


def test_an_invalid_pattern_is_refused_with_a_reason_and_not_stored(workbench_client):
    group = group_for(workbench_client, "v.1(1990)-v.5(1994)")
    body = workbench_client.put("/api/patterns", json={"patterns": [{
        "label": "bad", "regex": group["regex"],
        "roles": [{"group": "start_vol", "boundary": "start", "level": "vol"}],
    }]}).get_json()
    assert body["count"] == 0
    assert any("no role decided" in r for r in body["rejected"])


def test_a_library_survives_export_and_import_over_http(workbench_client):
    group = group_for(workbench_client, "v.1(1990)-v.5(1994)")
    confirm(workbench_client, group)

    exported = workbench_client.get("/api/patterns/export")
    assert exported.status_code == 200
    document = json.loads(exported.get_data())
    assert document["schema"] == 1
    assert len(document["patterns"]) == 1

    workbench_client.put("/api/patterns", json={"patterns": []})
    assert workbench_client.get("/api/patterns").get_json()["count"] == 0

    restored = workbench_client.post(
        "/api/patterns/import",
        data={"file": (io.BytesIO(exported.get_data()), "patterns.json")},
        content_type="multipart/form-data",
    ).get_json()
    assert restored["imported"] == 1
    assert restored["count"] == 1


def test_importing_something_that_is_not_a_library_is_refused(workbench_client):
    response = workbench_client.post(
        "/api/patterns/import",
        data={"file": (io.BytesIO(b"not json at all"), "patterns.json")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert "JSON" in response.get_json()["error"]


# ---------------------------------------------------------------------------
# Converting with the library applied
# ---------------------------------------------------------------------------

def test_previews_say_what_read_each_statement(workbench_client, example_marc_bytes):
    records = upload_marc(workbench_client, example_marc_bytes).get_json()["records"]
    idx = record_index_with(records, "v.6(1995)-")

    before = previews_for(workbench_client, idx)
    assert all(p["source"] == "parser" for p in before)
    assert all(p["source_label"] == "Standard parser" for p in before)

    confirm(workbench_client, group_for(workbench_client, "v.6(1995)-"))

    after = previews_for(workbench_client, idx)
    used = [p for p in after if p["source_866"] == "v.6(1995)-"]
    assert used and used[0]["from_pattern"] is True


def test_a_confirmed_pattern_converts_what_the_parser_held_for_review(
        workbench_client, messy_marc_bytes):
    """
    The reason to build this at all.

    "?: 16" is a number with nothing to say whether it is a volume or an issue,
    so the parser holds it back rather than guessing -- and it stays unconverted
    for ever, because no amount of parser work can supply information the
    statement does not contain. A cataloguer who knows the collection can.
    """
    records = upload_marc(workbench_client, messy_marc_bytes).get_json()["records"]
    idx = record_index_with(records, "?: 16")

    held = [p for p in previews_for(workbench_client, idx)
            if p["source_866"] == "?: 16"]
    assert held and not held[0]["fields_863"]

    group = group_for(workbench_client, "?: 16")
    number = next(g for g in group["named_groups"] if g.endswith("num"))
    confirm(workbench_client, group, decisions={number: ("start", "issue")})

    converted = [p for p in previews_for(workbench_client, idx)
                 if p["source_866"] == "?: 16"]
    assert converted[0]["from_pattern"] is True
    assert any("$b 16" in f for f in converted[0]["fields_863"])


def test_batch_conversion_reports_what_read_what(workbench_client, example_marc_bytes):
    upload_marc(workbench_client, example_marc_bytes)
    confirm(workbench_client, group_for(workbench_client, "v.6(1995)-"))

    body = workbench_client.post("/api/batch-convert", json=SETTINGS).get_json()
    assert body["success"] is True
    by_source = {s["source"]: s["count"] for s in body["by_source"]}
    assert by_source.get("confirmed-1", 0) >= 1
    assert by_source.get("parser", 0) >= 1


def test_conversion_is_downloadable(workbench_client, example_marc_bytes):
    upload_marc(workbench_client, example_marc_bytes)
    workbench_client.post("/api/batch-convert", json=SETTINGS)
    response = workbench_client.get("/api/download-converted")
    assert response.status_code == 200
    assert response.data.startswith(b"0")        # a MARC leader


def test_the_workbench_cannot_clobber_the_converters_session(workbench_client,
                                                            converter_client,
                                                            example_marc_bytes):
    """
    All three apps are served from one hostname, and Flask names its session
    cookie "session" at path / by default. With the stock name, a cataloguer who
    uploaded in the converter and then uploaded here would overwrite the
    converter's cookie -- and on going back would be told their file was gone,
    because the cookie no longer verifies against the converter's secret key.

    Ports do not save this either: cookies ignore them, so localhost:5000 and
    localhost:5003 share a jar just as the deployed paths share a hostname.
    """
    def cookie_name(response):
        return response.headers["Set-Cookie"].split("=", 1)[0]

    here = cookie_name(upload_marc(workbench_client, example_marc_bytes))
    there = cookie_name(upload_marc(converter_client, example_marc_bytes))

    assert here != there, (
        f"both apps set a cookie named {here!r}; one would overwrite the other"
    )


def test_turning_the_parser_off_leaves_unmatched_holdings_untouched(
        workbench_client, messy_marc_bytes):
    """
    With the parser switched off, a record whose statements no pattern matches
    converts to nothing and keeps every 866 it started with.
    """
    records = upload_marc(workbench_client, messy_marc_bytes).get_json()["records"]
    idx = record_index_with(records, "1993: (1 [Feb])")

    with_parser = previews_for(workbench_client, idx)
    assert any(p["fields_863"] for p in with_parser)

    body = workbench_client.post("/api/preview-record", json={
        "record_index": idx, "parser_fallback": False, **SETTINGS}).get_json()
    assert all(not p["fields_863"] for p in body["previews"])
    assert all(p["source"] == "unmatched" for p in body["previews"])
    assert body["previews"][0]["source_label"].startswith("No pattern matched")


def test_turning_the_parser_off_keeps_the_866_on_disk(workbench_client,
                                                      messy_marc_bytes):
    """The point of leaving it alone: the holdings are still there afterwards."""
    import io as _io
    from pymarc import MARCReader

    upload_marc(workbench_client, messy_marc_bytes)
    assert workbench_client.post("/api/batch-convert", json={
        **SETTINGS, "parser_fallback": False}).status_code == 200

    data = workbench_client.get("/api/download-converted").data
    statements = [
        (f["a"] or "").strip()
        for rec in MARCReader(_io.BytesIO(data)) if rec
        for f in rec.get_fields("866")
    ]
    assert "1993: (1 [Feb])" in statements
    assert "?: 16" in statements


def test_the_parser_is_used_unless_it_is_switched_off(workbench_client,
                                                      messy_marc_bytes):
    """Default behaviour is unchanged, which every other test depends on."""
    records = upload_marc(workbench_client, messy_marc_bytes).get_json()["records"]
    idx = record_index_with(records, "1993: (1 [Feb])")
    assert any(p["source"] == "parser" for p in previews_for(workbench_client, idx))


# ---------------------------------------------------------------------------
# Reviewing a file record by record
# ---------------------------------------------------------------------------

def test_a_page_of_records_previews_in_one_request(workbench_client,
                                                   example_marc_bytes):
    """
    Reviewing a file a record at a time needs the whole file, and a round trip
    per record is what makes that impractical -- previewing one costs well under
    a millisecond.
    """
    upload_marc(workbench_client, example_marc_bytes)
    body = workbench_client.post("/api/preview-records", json=SETTINGS).get_json()

    assert body["total"] == 5
    assert [r["index"] for r in body["records"]] == [0, 1, 2, 3, 4]
    assert all(r["title"] for r in body["records"])
    assert any(r["previews"] for r in body["records"])


def test_the_page_agrees_with_previewing_records_one_at_a_time(workbench_client,
                                                               example_marc_bytes):
    """
    The bulk route exists to save round trips, not to be a second opinion. It
    shares convert_record() with the single route, and this pins that.
    """
    upload_marc(workbench_client, example_marc_bytes)
    bulk = workbench_client.post("/api/preview-records", json=SETTINGS).get_json()

    for entry in bulk["records"]:
        if not entry["has_866"]:
            continue
        one = previews_for(workbench_client, entry["index"])
        assert [p["fields_863"] for p in entry["previews"]] == \
               [p["fields_863"] for p in one], entry["index"]
        assert [p["source"] for p in entry["previews"]] == [p["source"] for p in one]


def test_each_record_carries_the_counts_a_reviewer_filters_on(workbench_client,
                                                              messy_marc_bytes):
    """
    'Show me what is still held for review' has to be answerable without
    reopening every record, so the counts come back with the page.
    """
    upload_marc(workbench_client, messy_marc_bytes)
    body = workbench_client.post("/api/preview-records", json=SETTINGS).get_json()

    held = [r for r in body["records"] if r["held"]]
    assert held, "the messy corpus has statements no engine converts"
    assert all(r["converted"] + r["held"] == len(r["previews"])
               for r in body["records"])

    # The record carrying only unreadable statements converts none of them.
    unreadable = next(r for r in body["records"]
                      if any(p["source_866"] == "? 106" for p in r["previews"]))
    assert unreadable["converted"] < len(unreadable["previews"])

    # A record with no 866 at all is reported rather than omitted, so the page
    # stays in step with the file.
    empty = [r for r in body["records"] if not r["has_866"]]
    assert empty and all(r["previews"] == [] for r in empty)


def test_the_page_is_bounded_and_can_be_walked(workbench_client,
                                               messy_marc_bytes):
    upload_marc(workbench_client, messy_marc_bytes)
    first = workbench_client.post("/api/preview-records", json={
        **SETTINGS, "offset": 0, "limit": 4}).get_json()
    second = workbench_client.post("/api/preview-records", json={
        **SETTINGS, "offset": 4, "limit": 4}).get_json()

    assert [r["index"] for r in first["records"]] == [0, 1, 2, 3]
    assert [r["index"] for r in second["records"]] == [4, 5, 6, 7]
    assert first["total"] == second["total"]


def test_the_page_writes_nothing(workbench_client, example_marc_bytes):
    """The read-only twin of batch-convert: reviewing must not convert."""
    upload_marc(workbench_client, example_marc_bytes)
    before = workbench_client.get("/api/download-converted").data
    workbench_client.post("/api/preview-records", json=SETTINGS)
    assert workbench_client.get("/api/download-converted").data == before


def test_the_page_honours_the_parser_switch(workbench_client, messy_marc_bytes):
    upload_marc(workbench_client, messy_marc_bytes)
    off = workbench_client.post("/api/preview-records", json={
        **SETTINGS, "parser_fallback": False}).get_json()
    assert all(p["source"] == "unmatched"
               for r in off["records"] for p in r["previews"])


# ---------------------------------------------------------------------------
# The promise: nothing is sacrificed
# ---------------------------------------------------------------------------

def test_empty_library_matches_the_converter_exactly(workbench_client,
                                                     converter_client, any_corpus):
    """
    With no pattern confirmed, the workbench must be the converter.

    Byte for byte, over every corpus this machine can reach -- the synthetic two
    on a clean clone, plus the private files when the share is mounted. Anything
    less and merging the tools would have cost a cataloguer something, which is
    the one outcome this change is not allowed to have.
    """
    upload_marc(workbench_client, any_corpus)
    upload_marc(converter_client, any_corpus)

    assert workbench_client.get("/api/patterns").get_json()["count"] == 0

    assert workbench_client.post("/api/batch-convert", json=SETTINGS).status_code == 200
    assert converter_client.post("/api/batch-convert", json=SETTINGS).status_code == 200

    from_workbench = workbench_client.get("/api/download-converted").data
    from_converter = converter_client.get("/api/download-converted").data
    assert from_workbench == from_converter


def test_single_record_conversion_also_matches_the_converter(workbench_client,
                                                             converter_client,
                                                             messy_marc_bytes):
    """The record-level route has its own 866-stripping rules; check them too."""
    records = upload_marc(workbench_client, messy_marc_bytes).get_json()["records"]
    upload_marc(converter_client, messy_marc_bytes)

    idx = record_index_with(records, "?: 16")
    payload = {
        "record_index": idx,
        "conversions": [{"text": f["a"], "remove_866": True}
                        for f in records[idx]["fields_866"] if f["a"]],
        **SETTINGS,
    }
    assert workbench_client.post("/api/convert-record", json=payload).status_code == 200
    assert converter_client.post("/api/convert-record", json=payload).status_code == 200

    assert workbench_client.get("/api/download-converted").data == \
           converter_client.get("/api/download-converted").data
