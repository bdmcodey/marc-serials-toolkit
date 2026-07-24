"""
AI regex generator for MARC holdings statements.
Uses OpenAI to generate a Python regex that parses textual holdings into
the same capture group structure as the existing parser.
"""

import re
import os
from pattern_manager import (
    DEFAULT_YEAR_PATTERN,
    DEFAULT_CHRON_PATTERN,
    EXPECTED_GROUP_NAMES,
)

# Optional: load .env for OPENAI_API_KEY
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def create_regex_prompt(samples, expected_groups=None):
    """
    Build the prompt for OpenAI to generate a year_pattern regex.
    expected_groups: list of 7 names matching EXPECTED_GROUP_NAMES.
    """
    expected_groups = expected_groups or EXPECTED_GROUP_NAMES
    sample_texts = []
    for s in samples:
        text = s["text"] if isinstance(s, dict) else s
        sample_texts.append(text)

    prompt = f"""You are helping parse MARC 21 summary holdings statements (e.g., from field 866 subfield $a). The goal is to produce a single Python regular expression that extracts volume, issue, and chronology into exactly 7 capture groups, in this order:

1. vol1_no_issues - first volume number when the pattern is "v. N - v. M" with no issue numbers (e.g. "v. 1-14 (1953-1966)")
2. vol2_no_issues - second volume number in that same pattern
3. vol1 - first volume when issue numbers are present (e.g. "v. 8 no. 3-v. 10 no. 2 (1981-Fall 1983)")
4. iss1 - first issue number(s) (e.g. "3", "3-4", "1")
5. vol2 - second volume in a range
6. iss2 - second issue number in a range
7. chron - the chronology string inside parentheses (e.g. "1974-1976", "April 1992-April 1996", "Spring 1995")

The regex must use findall() and return a list of 7-tuples. Chronology is always the last group and is the content inside the final parentheses. Volume/issue prefixes may be "v." or "v", "no." or "no" or "nos."; numbers can be ranges (e.g. 1-3), comma-separated, or single. The chronology group should capture everything inside the closing parenthesis that contains the date/season/month.

Example of existing pattern (for reference only; your collection may need a different pattern):
{repr(DEFAULT_YEAR_PATTERN)}

Sample holdings statements from the collection to match:

"""
    for t in sample_texts[:50]:
        prompt += f"  {t!r}\n"
    prompt += """

Respond with ONLY a single line: the raw Python regex string (no re.compile, no explanation). Use r\"...\" style. The regex must have exactly 7 capturing groups in the order above. Output nothing else."""

    return prompt


def extract_regex_from_response(ai_response):
    """
    Parse the model response to get a single regex pattern string.
    Handles lines like r\"...\" or \"...\" and strips code blocks.
    """
    text = (ai_response or "").strip()
    # Remove markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    text = text.strip()
    # Take first line if multiple
    first_line = text.split("\n")[0].strip()
    # Remove r"..." or r'...' or "..." or '...'
    for prefix in ("r\"", "r'", "\"", "'"):
        if first_line.startswith(prefix):
            end = "\"" if "\"" in prefix or (prefix == "r\"" or prefix == "\"") else "'"
            start = len(prefix)
            last = first_line.rfind(end)
            if last > start:
                return first_line[start:last]
            return first_line[start:]
    return first_line


def generate_regex_from_samples(samples, capture_groups_spec=None, api_key=None):
    """
    Call OpenAI to generate a year_pattern regex from sample holdings.

    Args:
        samples: List of dicts with "text" or list of strings.
        capture_groups_spec: Optional list of 7 group names (default EXPECTED_GROUP_NAMES).
        api_key: Optional OpenAI API key; otherwise uses OPENAI_API_KEY env.

    Returns:
        dict with: success, year_pattern, chron_pattern, error.
        chron_pattern is left as default; only year_pattern is generated.
    """
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {
            "success": False,
            "year_pattern": None,
            "chron_pattern": DEFAULT_CHRON_PATTERN,
            "error": "OPENAI_API_KEY not set",
        }

    try:
        import openai
    except ImportError:
        return {
            "success": False,
            "year_pattern": None,
            "chron_pattern": DEFAULT_CHRON_PATTERN,
            "error": "openai package not installed (pip install openai)",
        }

    prompt = create_regex_prompt(samples, capture_groups_spec)
    try:
        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You output only a valid Python regex string with exactly 7 capture groups. No explanation."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        raw = response.choices[0].message.content
        year_pattern = extract_regex_from_response(raw)
        if not year_pattern:
            return {
                "success": False,
                "year_pattern": None,
                "chron_pattern": DEFAULT_CHRON_PATTERN,
                "error": "Could not extract regex from model response",
            }
        re.compile(year_pattern)
    except re.error as e:
        return {
            "success": False,
            "year_pattern": year_pattern if "year_pattern" in dir() else None,
            "chron_pattern": DEFAULT_CHRON_PATTERN,
            "error": f"Generated regex did not compile: {e}",
        }
    except Exception as e:
        return {
            "success": False,
            "year_pattern": None,
            "chron_pattern": DEFAULT_CHRON_PATTERN,
            "error": str(e),
        }

    return {
        "success": True,
        "year_pattern": year_pattern,
        "chron_pattern": DEFAULT_CHRON_PATTERN,
        "error": None,
    }
