"""
Pattern detector HTTP routes.

The last test in this file is the one that matters most: it takes a regex the
detector generated and feeds it to the detector's own Test button. That
round-trip is the workflow the UI performs, and it is the entire reason
MAX_PATTERN_TOKENS is set where it is -- a pattern the tool cannot test is a
pattern the cataloguer cannot trust.
"""

from __future__ import annotations

import pytest

from conftest import upload_marc


def test_detect_groups_statements(detector_client):
    response = detector_client.post("/api/detect", json={
        "statements": ["v.1(1990)-v.3(1992)", "v.5(1994)-v.8(1997)"],
    })
    assert response.status_code == 200

    body = response.get_json()
    assert body["total_statements"] == 2
    assert body["total_patterns"] == 1
    assert body["groups"][0]["match_rate"] == 1.0


def test_detect_requires_statements(detector_client):
    assert detector_client.post("/api/detect", json={"statements": []}).status_code == 400


def test_detect_rejects_only_whitespace(detector_client):
    response = detector_client.post("/api/detect", json={"statements": ["  ", ""]})
    assert response.status_code == 400


def test_split_option_is_honoured(detector_client):
    """
    Splitting is the default. Turning it off has to leave the statement whole,
    since a cataloguer may be looking at exactly how it was recorded.
    """
    statement = "v.1(1990)-v.3(1992), v.5(1994)-"

    split = detector_client.post("/api/detect", json={
        "statements": [statement], "split_multi_range": True}).get_json()
    whole = detector_client.post("/api/detect", json={
        "statements": [statement], "split_multi_range": False}).get_json()

    assert split["total_statements"] == 2
    assert whole["total_statements"] == 1


def test_upload_extracts_statements(detector_client, example_marc_bytes):
    response = upload_marc(detector_client, example_marc_bytes)
    assert response.status_code == 200

    body = response.get_json()
    assert body["count"] == 9          # nine 866 $a values across five records
    assert len(body["statements"]) == 9


def test_test_regex_reports_matches(detector_client):
    response = detector_client.post("/api/test-regex", json={
        "regex": r"v\.(?P<vol>\d+)\((?P<year>\d{4})\)",
        "statements": ["v.1(1990)", "v.2(1991)", "nope"],
    })
    assert response.status_code == 200

    body = response.get_json()
    assert body["matched"] == 2
    assert body["failed"] == 1


def test_test_regex_requires_a_pattern(detector_client):
    assert detector_client.post("/api/test-regex",
                                json={"statements": ["v.1(1990)"]}).status_code == 400


def test_invalid_regex_is_a_400_not_a_500(detector_client):
    """A user typing a broken pattern is expected input, not a server fault."""
    response = detector_client.post("/api/test-regex",
                                    json={"regex": "(", "statements": ["v.1(1990)"]})
    assert response.status_code == 400
    assert "error" in response.get_json()


@pytest.mark.parametrize("length, expected_status", [(2000, 200), (2001, 400)])
def test_regex_length_limit(detector_client, length, expected_status):
    """
    The 2,000-character ceiling is what MAX_PATTERN_TOKENS is calibrated
    against, so the boundary is pinned on both sides.
    """
    regex = "a" * length
    response = detector_client.post("/api/test-regex",
                                    json={"regex": regex, "statements": ["aaa"]})
    assert response.status_code == expected_status


def test_generated_regexes_survive_the_tools_own_test_button(detector_client,
                                                             example_marc_bytes):
    """
    The contract that ties the two endpoints together. Every regex the detector
    emits must be short enough for /api/test-regex to accept and must match the
    statements it was generated from -- otherwise the tool contradicts itself in
    front of the cataloguer.
    """
    statements = upload_marc(detector_client, example_marc_bytes).get_json()["statements"]
    groups = detector_client.post("/api/detect",
                                  json={"statements": statements}).get_json()["groups"]

    tested = 0
    for group in groups:
        if group["too_complex"]:
            continue
        response = detector_client.post("/api/test-regex", json={
            "regex": group["regex"], "statements": group["examples"]})
        assert response.status_code == 200, group["regex"][:80]
        assert response.get_json()["match_rate"] == 1.0
        tested += 1

    assert tested, "no testable groups were produced; the assertion proved nothing"
