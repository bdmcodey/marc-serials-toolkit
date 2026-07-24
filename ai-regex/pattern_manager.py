"""
Pattern manager for MARC holdings regex patterns.
Stores/loads AI-generated patterns and provides default patterns.
"""

import json
import re
from pathlib import Path
from datetime import datetime

# Default patterns (same as in test_enum_update.parse_holdings)
DEFAULT_CHRON_PATTERN = r"\(([^)]+)\)"
DEFAULT_YEAR_PATTERN = (
    r"(?:v.\s*(\d+)\s*-\s*(\d+))|(?:(?:v\.\s*(\d+):*)?\s*(?:(?:n|N)os?\.?\s*(\d+(?:-\d+)?(?:,\s*\d+(?:-\d+)?)*(?:/\d*)?(?:\s*-\s*\d+)?))?(?:\s*-\s*(?:v\.\s*(\d+)\s*(?:(?:n|N)os?\.?\s*(\d+(?:-\d+)?(?:,\s*\d+(?:-\d+)?)*(?:/\d*)?))?))?)\s*\(([^)]+)\)"
)

PATTERNS_DIR = Path(__file__).resolve().parent / "patterns"
COLLECTION_PATTERNS_FILE = PATTERNS_DIR / "collection_patterns.json"

# Expected capture group names for year_pattern (7 groups)
EXPECTED_GROUP_NAMES = [
    "vol1_no_issues",
    "vol2_no_issues",
    "vol1",
    "iss1",
    "vol2",
    "iss2",
    "chron",
]


def _ensure_patterns_dir():
    PATTERNS_DIR.mkdir(parents=True, exist_ok=True)


def _load_all_patterns():
    """Load the full collection_patterns.json. Returns dict keyed by collection_id."""
    if not COLLECTION_PATTERNS_FILE.exists():
        return {}
    try:
        with open(COLLECTION_PATTERNS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, IOError):
        return {}


def _save_all_patterns(patterns_dict):
    _ensure_patterns_dir()
    with open(COLLECTION_PATTERNS_FILE, "w", encoding="utf-8") as f:
        json.dump(patterns_dict, f, indent=2)


def get_default_pattern():
    """Return default chron and year pattern strings (and compiled regexes)."""
    return {
        "chron_pattern": DEFAULT_CHRON_PATTERN,
        "year_pattern": DEFAULT_YEAR_PATTERN,
        "chron_re": re.compile(DEFAULT_CHRON_PATTERN),
        "year_re": re.compile(DEFAULT_YEAR_PATTERN),
    }


def pattern_exists(collection_id):
    """Check if a pattern has been saved for this collection."""
    all_p = _load_all_patterns()
    return (collection_id or "").strip() in all_p


def load_pattern(collection_id):
    """
    Load saved pattern for collection_id.
    Returns dict with chron_pattern, year_pattern, chron_re, year_re, and metadata,
    or None if not found.
    """
    all_p = _load_all_patterns()
    raw = all_p.get((collection_id or "").strip())
    if not raw:
        return None
    try:
        chron_re = re.compile(raw["chron_pattern"])
        year_re = re.compile(raw["year_pattern"])
    except re.error:
        return None
    return {
        "chron_pattern": raw["chron_pattern"],
        "year_pattern": raw["year_pattern"],
        "chron_re": chron_re,
        "year_re": year_re,
        "generated_date": raw.get("generated_date"),
        "validation_stats": raw.get("validation_stats"),
    }


def save_pattern(collection_id, year_pattern, chron_pattern=None, metadata=None):
    """
    Save a generated pattern for the collection.
    chron_pattern defaults to DEFAULT_CHRON_PATTERN if not provided.
    """
    collection_id = (collection_id or "").strip()
    if not collection_id:
        raise ValueError("collection_id is required")
    chron_pattern = chron_pattern or DEFAULT_CHRON_PATTERN
    try:
        re.compile(year_pattern)
        re.compile(chron_pattern)
    except re.error as e:
        raise ValueError(f"Invalid regex: {e}")

    all_p = _load_all_patterns()
    entry = {
        "collection_id": collection_id,
        "chron_pattern": chron_pattern,
        "year_pattern": year_pattern,
        "generated_date": datetime.utcnow().isoformat() + "Z",
    }
    if metadata:
        entry["validation_stats"] = metadata.get("validation_stats")
    all_p[collection_id] = entry
    _save_all_patterns(all_p)
