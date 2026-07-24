"""
Regex validator for MARC holdings patterns.
Tests generated regex against sample holdings and validates capture group structure.
"""

import re
from pattern_manager import EXPECTED_GROUP_NAMES


def validate_regex(regex_pattern, samples, chron_pattern=None):
    """
    Test regex against all samples. Expects year_pattern to have 7 capture groups
    (vol1_no_issues, vol2_no_issues, vol1, iss1, vol2, iss2, chron).

    Args:
        regex_pattern: Python regex string for the main holdings (year) pattern.
        samples: List of dicts with "text" key, or list of strings.
        chron_pattern: Optional regex string for chronology; used to compute holdings_format.

    Returns:
        dict with: compiled, success, failed, results, error.
        results is list of { "text", "record_index", "matches", "holdings_format", "valid" }.
    """
    out = {
        "compiled": False,
        "success": [],
        "failed": [],
        "results": [],
        "error": None,
    }
    try:
        year_re = re.compile(regex_pattern)
        out["compiled"] = True
    except re.error as e:
        out["error"] = str(e)
        return out

    chron_re = re.compile(chron_pattern) if chron_pattern else None
    if not chron_pattern:
        chron_re = re.compile(r"\(([^)]+)\)")

    for i, s in enumerate(samples):
        text = s["text"] if isinstance(s, dict) else s
        rec_idx = s.get("record_index", i + 1) if isinstance(s, dict) else i + 1
        matches = year_re.findall(text)
        holdings_format = len(chron_re.findall(text)) if chron_re else 0

        valid, group_error = validate_capture_groups(matches, EXPECTED_GROUP_NAMES)
        entry = {
            "text": text,
            "record_index": rec_idx,
            "matches": matches,
            "holdings_format": holdings_format,
            "valid": valid,
            "group_error": group_error,
        }
        out["results"].append(entry)
        if valid and (matches or holdings_format == 0):
            out["success"].append(entry)
        else:
            out["failed"].append(entry)

    return out


def validate_capture_groups(matches, expected_groups=None):
    """
    Ensure matches (list of tuples from findall) have the expected structure.
    expected_groups: list of 7 names. Each match must be a tuple of 7 elements.

    Returns:
        (bool valid, str or None error_message)
    """
    expected_groups = expected_groups or EXPECTED_GROUP_NAMES
    if len(expected_groups) != 7:
        return False, f"Expected 7 group names, got {len(expected_groups)}"

    for m in matches:
        if not isinstance(m, (tuple, list)):
            return False, "Match is not a tuple or list"
        if len(m) != 7:
            return False, f"Match has {len(m)} groups, expected 7"
    return True, None


def generate_validation_report(regex_pattern, samples, chron_pattern=None):
    """
    Generate a report showing success rate, failed samples, and extraction examples.

    Returns:
        dict with: success_rate, total, passed, failed_list, example_extractions, report_text.
    """
    v = validate_regex(regex_pattern, samples, chron_pattern)
    total = len(samples)
    passed = len(v["success"])
    failed_list = [
        {"record_index": r["record_index"], "text": r["text"], "reason": r.get("group_error") or "no match or invalid groups"}
        for r in v["failed"]
    ]
    example_extractions = []
    for r in v["success"][:5]:
        if r.get("matches"):
            m = r["matches"][0]
            example_extractions.append({
                "text": r["text"],
                "vol1_no_issues": m[0],
                "vol2_no_issues": m[1],
                "vol1": m[2],
                "iss1": m[3],
                "vol2": m[4],
                "iss2": m[5],
                "chron": m[6],
            })

    success_rate = (passed / total * 100) if total else 0
    report_lines = [
        "=== Validation Report ===",
        f"Success rate: {passed}/{total} ({success_rate:.1f}%)",
        "",
        "Sample extractions (first 5):",
    ]
    for ex in example_extractions:
        report_lines.append(f"  Text: {ex['text']}")
        report_lines.append(f"    -> vol1_no_issues={ex['vol1_no_issues']!r}, vol2_no_issues={ex['vol2_no_issues']!r}, vol1={ex['vol1']!r}, iss1={ex['iss1']!r}, vol2={ex['vol2']!r}, iss2={ex['iss2']!r}, chron={ex['chron']!r}")
    if v["failed"]:
        report_lines.append("")
        report_lines.append("Failed samples:")
        for f in failed_list[:20]:
            report_lines.append(f"  Record {f['record_index']}: {f['text'][:80]!r} - {f.get('reason', '')}")
        if len(failed_list) > 20:
            report_lines.append(f"  ... and {len(failed_list) - 20} more.")
    if v.get("error"):
        report_lines.append("")
        report_lines.append(f"Compilation error: {v['error']}")

    report_text = "\n".join(report_lines)
    return {
        "success_rate": success_rate,
        "total": total,
        "passed": passed,
        "failed_list": failed_list,
        "example_extractions": example_extractions,
        "report_text": report_text,
        "compiled": v["compiled"],
        "validation_result": v,
    }
