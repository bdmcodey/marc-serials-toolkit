"""
Converter HTTP routes, driven through the Flask test client.

Uploaded MARC lives on disk keyed by a uuid in the session cookie, so a fresh
test client is a fresh working set. Two of the tests below depend on that and
say so; the rest simply benefit from it.
"""

from __future__ import annotations

import io

import pytest
from pymarc import MARCReader

from conftest import upload_marc


def _records(data: bytes) -> list:
    return [r for r in MARCReader(io.BytesIO(data)) if r is not None]


# ---------------------------------------------------------------------------
# Single-statement parsing
# ---------------------------------------------------------------------------

def test_parse_text_returns_a_preview(converter_client):
    response = converter_client.post("/api/parse-text",
                                     json={"text": "v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)"})
    assert response.status_code == 200

    body = response.get_json()
    assert body["parse"]["success"] is True
    assert body["preview"]["field_853"]
    assert body["preview"]["fields_863"]


def test_parse_text_requires_text(converter_client):
    response = converter_client.post("/api/parse-text", json={"text": ""})
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_parse_text_reports_rejected_convention_overrides(converter_client):
    """
    A bad subfield code must come back as a warning rather than being applied.
    This route is the only place those rejections surface.
    """
    response = converter_client.post("/api/parse-text", json={
        "text": "v.1(1990)-v.3(1992)",
        "convention": "standard",
        "subfields": {"vol": "z"},
    })
    assert response.status_code == 200
    warnings = response.get_json()["conversion"]["warnings"]
    assert any("$a-$m" in w for w in warnings)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def test_upload_lists_records(converter_client, example_marc_bytes):
    response = upload_marc(converter_client, example_marc_bytes)
    assert response.status_code == 200

    body = response.get_json()
    assert body["total"] == 5
    first = body["records"][0]
    assert set(first) >= {"index", "title", "issn", "location",
                          "fields_866", "has_853", "has_863"}
    assert first["title"] == "Journal of Imaginary Studies."


def test_upload_without_a_file_is_rejected(converter_client):
    response = converter_client.post("/api/upload-marc", data={},
                                     content_type="multipart/form-data")
    assert response.status_code == 400


def test_upload_of_non_marc_bytes_fails_as_json(converter_client):
    """
    Even a hard failure has to come back as JSON -- the UI parses the response,
    and an HTML traceback page would surface as an unhelpful "unexpected token".
    """
    response = upload_marc(converter_client, b"this is not a MARC file at all")
    assert response.status_code >= 400
    assert response.is_json
    assert "error" in response.get_json()


@pytest.mark.parametrize("route", ["/api/convert-record", "/api/preview-record",
                                   "/api/batch-convert"])
def test_conversion_routes_require_an_upload_first(converter_client, route):
    response = converter_client.post(route, json={"record_index": 0})
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_uploads_do_not_leak_between_clients(converter_app, example_marc_bytes,
                                             messy_marc_bytes, tmp_path, monkeypatch):
    """
    Each client carries its own cookie jar, so each gets its own session and its
    own file on disk. This is what lets the rest of the suite upload freely
    without tests treading on each other.
    """
    upload_dir = tmp_path / "shared-uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(converter_app, "UPLOAD_DIR", str(upload_dir))
    converter_app.app.config.update(TESTING=True, SECRET_KEY="test-secret-key")

    # Not `with` blocks: two nested test-client contexts unwind out of order.
    # Plain clients are enough here, since nothing inspects the request context.
    first = converter_app.app.test_client()
    second = converter_app.app.test_client()

    upload_marc(first, example_marc_bytes)
    upload_marc(second, messy_marc_bytes)

    assert first.post("/api/batch-convert", json={}).get_json()["records_processed"] == 5
    assert second.post("/api/batch-convert", json={}).get_json()["records_processed"] == 9


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def test_preview_does_not_modify_stored_data(converter_client, example_marc_bytes):
    """
    Preview promises to be read-only. If it ever wrote, a cataloguer clicking
    through records would silently accumulate conversions they never confirmed.
    """
    upload_marc(converter_client, example_marc_bytes)
    before = converter_client.get("/api/download-converted").data

    response = converter_client.post("/api/preview-record", json={"record_index": 0})
    assert response.status_code == 200

    after = converter_client.get("/api/download-converted").data
    assert after == before


def test_preview_pairs_each_statement_with_its_source(converter_client, example_marc_bytes):
    upload_marc(converter_client, example_marc_bytes)
    body = converter_client.post("/api/preview-record",
                                 json={"record_index": 0}).get_json()
    assert body["previews"]
    for preview in body["previews"]:
        assert preview["source_866"]


# ---------------------------------------------------------------------------
# Batch conversion and download
# ---------------------------------------------------------------------------

def test_batch_convert_summarises_every_record(converter_client, example_marc_bytes):
    upload_marc(converter_client, example_marc_bytes)
    body = converter_client.post("/api/batch-convert",
                                 json={"convention": "standard"}).get_json()

    assert body["success"] is True
    assert body["records_processed"] == 5
    # Record 3 converts one of its two statements: "39 no 1 (Spring 1995)" reads
    # as v.39 no.1, while "34 no 3, 4 (Summer, Autumn 1990)" is still beyond the
    # parser and keeps its 866.
    assert [s["converted_fields"] for s in body["summary"]] == [2, 1, 2, 1, 2]


def test_download_returns_valid_marc(converter_client, example_marc_bytes):
    upload_marc(converter_client, example_marc_bytes)
    converter_client.post("/api/batch-convert", json={"convention": "standard"})

    response = converter_client.get("/api/download-converted")
    assert response.status_code == 200
    assert response.mimetype == "application/marc"
    assert "holdings_converted.mrc" in response.headers["Content-Disposition"]

    records = _records(response.data)
    assert len(records) == 5
    assert records[0].get_fields("853")
    assert records[0].get_fields("863")


def test_download_before_upload_is_not_found(converter_client):
    assert converter_client.get("/api/download-converted").status_code == 404


def test_batch_convert_is_idempotent(converter_client, example_marc_bytes):
    """
    Converting a second time must not stack another set of 863s on top of the
    first. Byte equality is the strongest form of this and currently holds.
    """
    upload_marc(converter_client, example_marc_bytes)
    converter_client.post("/api/batch-convert", json={"convention": "standard"})
    first = converter_client.get("/api/download-converted").data

    converter_client.post("/api/batch-convert", json={"convention": "standard"})
    second = converter_client.get("/api/download-converted").data

    assert first == second


def test_keeping_the_source_866_is_possible(converter_client, example_marc_bytes):
    upload_marc(converter_client, example_marc_bytes)
    converter_client.post("/api/batch-convert",
                          json={"convention": "standard", "remove_866": False})

    records = _records(converter_client.get("/api/download-converted").data)
    assert records[0].get_fields("866")
    assert records[0].get_fields("853")


def test_messy_corpus_converts_without_error(converter_client, messy_marc_bytes):
    """The awkward corpus must survive the whole pipeline, warnings and all."""
    upload_marc(converter_client, messy_marc_bytes)
    body = converter_client.post("/api/batch-convert", json={}).get_json()

    assert body["success"] is True
    # Nine of ten: "Index of Absent Holdings" carries no 866 at all and is
    # skipped before conversion rather than summarised as a zero-field record.
    assert body["records_processed"] == 9
    assert len(_records(converter_client.get("/api/download-converted").data)) == 10


# ---------------------------------------------------------------------------
# Per-statement 866 stripping (0.5.2)
#
# Stripping used to be decided for the whole record, which destroyed holdings
# the parser could not read. These pin the per-statement rule from both routes.
# ---------------------------------------------------------------------------

def test_batch_keeps_the_866_of_an_unconverted_statement(converter_client,
                                                         messy_marc_bytes):
    """
    Messy record 5 carries three statements: "?: 16" and "? 106" are held for
    review, "2016?" converts. Only the converted one's 866 may be removed.
    """
    upload_marc(converter_client, messy_marc_bytes)
    converter_client.post("/api/batch-convert", json={})

    record = _records(converter_client.get("/api/download-converted").data)[5]
    remaining = sorted((f["a"] or "") for f in record.get_fields("866"))

    assert remaining == ["? 106", "?: 16"]
    assert record.get_fields("863")          # the converted one did convert


def test_single_record_route_keeps_unconverted_866s(converter_client,
                                                    example_marc_bytes):
    """
    The single-statement route used to strip every 866 before conversion had
    even run, so it destroyed review statements the batch route protected.
    """
    upload_marc(converter_client, example_marc_bytes)
    response = converter_client.post("/api/convert-record", json={
        "record_index": 3,
        "conversions": [{"text": "34 no 3, 4 (Summer, Autumn 1990)"},
                        {"text": "39 no 1 (Spring 1995)"}],
    })
    assert response.status_code == 200

    # The statement that converted has its 866 removed; the one that did not
    # keeps its own. Asserting which field survived rather than how many says
    # what the route is actually for.
    record = _records(converter_client.get("/api/download-converted").data)[3]
    surviving = [(f["a"] or "").strip() for f in record.get_fields("866")]
    assert surviving == ["34 no 3, 4 (Summer, Autumn 1990)"]


def test_an_edited_statement_never_deletes_an_866(converter_client,
                                                  example_marc_bytes):
    """
    Statement text arrives from the client and may have been edited in the UI.
    A spec matching no 866 on the record must leave every field alone rather
    than guessing which one it meant.
    """
    upload_marc(converter_client, example_marc_bytes)
    converter_client.post("/api/convert-record", json={
        "record_index": 0,
        "conversions": [{"text": "v.99(2099)-v.100(2100)"}],   # not on the record
    })

    record = _records(converter_client.get("/api/download-converted").data)[0]
    assert len(record.get_fields("866")) == 2
