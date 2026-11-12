"""Splits one pasted blob of text (typically a ChatGPT response) into (title, description)
for Quick Paste. A line is recognised as the Title or Description heading by its leading
keyword alone -- "title" / "description" -- tolerating whatever decoration surrounds it
(markdown bold, #/-/bullet/number prefixes, an optional "+ Hashtags" or parenthetical
character-count suffix, an optional colon). Position, line count, and character count never
decide the split, only which heading line was matched, so this keeps working across minor
formatting drift between responses.

Expected shape (any of these headings, and blank lines around them, are fine):

    Title + Hashtags (<=200 characters): BF5 Panzerfaust Hangar Double Kill #BattlefieldV #Shorts

    Description: Running toward the enemy objective with the Panzerfaust...
    Reach the outside of the hangar and spot an enemy...

    **Title:**
    BF5 Panzerfaust Hangar Double Kill #BattlefieldV #Shorts

    **Description:**
    Running toward the enemy objective with the Panzerfaust...
"""
from __future__ import annotations

import re
from typing import Optional

# YouTube's actual API limits (snippet.title / snippet.description) -- not the "<=200
# characters" some ChatGPT prompt templates ask for, which is already over the real title
# limit and produces a cryptic HttpError 400 "invalidTitle" if never checked client-side.
MAX_TITLE_LEN = 100
MAX_DESCRIPTION_LEN = 5000

# Strips leading list/heading decoration (#, >, bullets, "1.", "1)", markdown bold/italic
# markers) from a line before checking whether it's a Title/Description heading -- so
# "### **Title:**" and "- Title:" are recognised exactly like a plain "Title:".
_LEADING_DECORATION_RE = re.compile(r"^[\s#>•\-*_]*(?:\d+[.)][\s#>•\-*_]*)?")

# A heading line consists of the keyword, an optional "+ Hashtags"/"and Hashtags" joiner
# (title only), an optional "(...)" aside (e.g. a character-count reminder), an optional
# colon, and stray markdown bold/italic markers around any of those -- then whatever's left
# on the line is the inline content, which may be empty if the content is on the next line.
_TITLE_HEADING_RE = re.compile(
    r"^title\b[*_]*(?:\s*(?:[+&]|and)\s*[*_]*hashtags?)?[*_]*\s*(?:\([^)]*\))?[*_]*\s*:?[*_]*\s*(.*)$",
    re.IGNORECASE,
)
_DESCRIPTION_HEADING_RE = re.compile(
    r"^desc(?:ription)?\b[*_]*\s*:?[*_]*\s*(.*)$",
    re.IGNORECASE,
)


def parse_title_description(text: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Returns (title, description, error). Exactly one of (title and description) or
    error is set -- never guesses a split when a heading is missing or out of order."""
    lines = text.split("\n")

    title_idx: Optional[int] = None
    title_inline = ""
    desc_idx: Optional[int] = None
    desc_inline = ""

    for i, line in enumerate(lines):
        stripped = _LEADING_DECORATION_RE.sub("", line, count=1)
        if title_idx is None:
            m = _TITLE_HEADING_RE.match(stripped)
            if m:
                title_idx, title_inline = i, m.group(1).strip()
                continue
        elif desc_idx is None:
            m = _DESCRIPTION_HEADING_RE.match(stripped)
            if m:
                desc_idx, desc_inline = i, m.group(1).strip()

    if title_idx is None or desc_idx is None:
        missing = []
        if title_idx is None:
            missing.append('a "Title" section')
        if desc_idx is None:
            missing.append('a "Description" section')
        return None, None, (
            "Unable to detect title or description.\nPlease make sure the input contains "
            + " and ".join(missing) + "."
        )

    title_lines = ([title_inline] if title_inline else []) + [
        l.strip() for l in lines[title_idx + 1 : desc_idx] if l.strip()
    ]
    title = " ".join(title_lines).strip()

    desc_lines = ([desc_inline] if desc_inline else []) + lines[desc_idx + 1 :]
    description = "\n".join(desc_lines).strip()

    if not title:
        return None, None, 'A "Title" heading was found, but no title text follows it.'
    if not description:
        return None, None, 'A "Description" heading was found, but no description text follows it.'

    return title, description, None
