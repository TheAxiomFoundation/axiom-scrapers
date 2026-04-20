"""Text normalization helpers shared across scrapers.

Most state legislatures export statutes from Word/ColdFusion/ASP, leaving
inline HTML noise — soft-break artifacts, font spans, literal ``&#160;``,
Windows-1252 dashes. Normalizing once here keeps per-jurisdiction parsers
terse and makes the AKN-3.0 output predictable.
"""

from __future__ import annotations

import html as _html
import re


def clean_text(s: str) -> str:
    """Return whitespace-normalized visible text from an HTML fragment.

    Behavior:
    * ``<br>`` / ``<br/>`` become newlines (paragraph-ish breaks).
    * Block-close tags (``</p>``, ``</div>``, ``</tr>``, ``</td>``, ``</span>``)
      also become newlines so Word-exported inline markup collapses into
      readable paragraph stacks.
    * Remaining tags are stripped.
    * HTML entities are decoded; ``\\xa0`` (U+00A0) is replaced with a
      regular space to match what we'd get from a plain-text paste.
    * Tabs and runs of spaces collapse to a single space; runs of
      newlines collapse to a single newline.
    * Leading / trailing whitespace is trimmed.

    Empty or whitespace-only inputs return ``""``.
    """
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</(p|div|tr|td|span|li)>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n+", "\n", s).strip()
    return s


def split_paragraphs(s: str) -> list[str]:
    """Split normalized text into non-empty paragraphs on blank-line gaps.

    Consistent with how downstream AKN emits a ``<p>`` per paragraph.
    """
    return [p for p in re.split(r"\n\n+", s) if p.strip()]


def safe_path_segment(s: str) -> str:
    """Turn a section identifier into a safe filename segment.

    Replaces ``/`` with ``_`` (breaks paths) but leaves dots, dashes,
    letters, digits, and colons alone — those are common in state
    citation formats (e.g. DC ``47-1801.04``, NV ``244.010``, RI
    ``1-1-17.1``).
    """
    return s.replace("/", "_").strip()
