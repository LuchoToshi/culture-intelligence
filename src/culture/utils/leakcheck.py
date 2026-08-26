"""Mechanical source-name leak detection for reader-facing text.

The prompts carry the disclosure rule (culture.analysis.disclosure); this is
the check on top of it, for when a model slips anyway. Two design points,
both learned the hard way:

- Word-boundary matching, not a length filter. The old check skipped names
  of three characters or fewer, which permanently excluded NTS and i-D from
  detection. A custom boundary (no word character or hyphen on either side)
  lets short names match as words without firing inside "moments" or
  "hi-definition".

- Active sources only. The discovery pipeline creates candidate Source rows
  for things that are not observers at all — "Met Gala" and "World Cup" both
  exist as inactive candidates. Scanning against those would flag legitimate
  cultural commentary. The confidentiality obligation covers the sources we
  actually monitor.
"""

from __future__ import annotations

import re

# A name matches only when not embedded in a longer word. \b fails on names
# with leading/trailing non-word characters (i-D), so the boundary is spelled
# out: nothing word-like or hyphen on either side.
_BOUNDARY = r"(?<![\w-]){}(?![\w-])"

MIN_NAME_LENGTH = 3  # below this ("i", "SZ") even word-bounded matching is noise


def compile_patterns(names: list[str]) -> dict[str, re.Pattern[str]]:
    return {
        name: re.compile(_BOUNDARY.format(re.escape(name)), re.IGNORECASE)
        for name in {n.strip() for n in names}
        if len(name.strip()) >= MIN_NAME_LENGTH
    }


def find_leaks(text: str, patterns: dict[str, re.Pattern[str]]) -> list[str]:
    """Names from `patterns` that appear in `text`, sorted, each at most once."""
    return sorted(name for name, pat in patterns.items() if pat.search(text))
