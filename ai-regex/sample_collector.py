"""
Sample collector for MARC holdings statements.
Extracts 866 $a subfields from MARC records for AI regex analysis.
"""

from pymarc import MARCReader
import re
from collections import defaultdict
from pathlib import Path


def collect_holdings_samples(marc_file, max_samples=50):
    """
    Extract 866 $a subfields from MARC records in a file.

    Args:
        marc_file: Path to .mrc file (binary MARC). For .mrk files, use
            collect_holdings_from_mrk() or convert first.
        max_samples: Maximum number of holdings strings to collect.

    Returns:
        List of (raw_string, record_index) for each 866 $a value.
    """
    samples = []
    path = Path(marc_file)

    if not path.exists():
        raise FileNotFoundError(f"MARC file not found: {marc_file}")

    # Only binary MARC supported by MARCReader
    if path.suffix.lower() == ".mrk":
        return collect_holdings_from_mrk(marc_file, max_samples)

    with open(marc_file, "rb") as f:
        reader = MARCReader(f)
        for i, record in enumerate(reader, 1):
            if record is None:
                continue
            if len(samples) >= max_samples:
                break
            for field in record.get_fields("866"):
                for subfield_a in field.get_subfields("a"):
                    s = subfield_a.strip()
                    if s:
                        samples.append({"text": s, "record_index": i})
                        if len(samples) >= max_samples:
                            break
            if len(samples) >= max_samples:
                break

    return samples


def collect_holdings_from_mrk(mrk_file, max_samples=50):
    """
    Extract 866 $a content from a MARC .mrk (text) file.

    Lines like: =866  \\0$av. 1-3 (1974-1976)
    """
    samples = []
    path = Path(mrk_file)
    if not path.exists():
        raise FileNotFoundError(f"MRK file not found: {mrk_file}")

    # Simple line-based extraction for =866 ... $a...
    pattern = re.compile(r"^=866\s+.*?\$a(.*)$")
    record_index = 0
    with open(mrk_file, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n\r")
            if line.startswith("=LDR"):
                record_index += 1
            if line.startswith("=866") and "$a" in line:
                m = pattern.match(line)
                if m:
                    s = m.group(1).strip()
                    if s:
                        samples.append({"text": s, "record_index": record_index})
                        if len(samples) >= max_samples:
                            break
    return samples


def analyze_pattern_diversity(samples):
    """
    Identify if samples follow a consistent pattern (e.g., all have parentheses for chronology).

    Returns:
        dict with keys like: has_chron_parens, has_volume_prefix, has_issue_prefix, sample_count.
    """
    if not samples:
        return {"sample_count": 0}

    texts = [s["text"] if isinstance(s, dict) else s for s in samples]
    has_chron_parens = sum(1 for t in texts if "(" in t and ")" in t)
    has_volume = sum(1 for t in texts if re.search(r"\bv\.?\s*\d+", t, re.I))
    has_issue = sum(1 for t in texts if re.search(r"\b(?:no\.?|nos\.?|number)\s*\d+", t, re.I))
    has_year = sum(1 for t in texts if re.search(r"\b\d{4}\b", t))

    return {
        "sample_count": len(texts),
        "with_chronology_parens": has_chron_parens,
        "with_volume": has_volume,
        "with_issue": has_issue,
        "with_year": has_year,
        "all_have_chron_parens": has_chron_parens == len(texts),
    }


def group_by_pattern(samples):
    """
    Group samples that likely follow the same pattern (e.g., same number of chronology parens).

    Returns:
        dict mapping a simple pattern key to list of sample indices or sample dicts.
    """
    if not samples:
        return {}

    def pattern_key(s):
        text = s["text"] if isinstance(s, dict) else s
        n_parens = text.count("(")  # chronology often in parens
        has_vol = "1" if re.search(r"\bv\.?\s*\d+", text, re.I) else "0"
        has_no = "1" if re.search(r"\b(?:no\.?|nos?\.?)\s*\d+", text, re.I) else "0"
        return (n_parens, has_vol, has_no)

    groups = defaultdict(list)
    for i, s in enumerate(samples):
        key = pattern_key(s)
        groups[key].append(s if isinstance(s, dict) else {"text": s, "record_index": i + 1})

    return dict(groups)
