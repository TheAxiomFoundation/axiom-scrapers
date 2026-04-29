"""Neutral source-section output for scraper results.

Scrapers normalize source pages into ``SourceSection`` objects, then the
runner writes paired text and metadata files:

* ``<section>.txt`` contains the source text used for ingestion.
* ``<section>.meta.yaml`` contains provenance and citation metadata.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

from .text import split_paragraphs

# Strip C0 control characters that commonly leak from PDF or HTML sources.
_INVALID_TEXT_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def strip_invalid_control_chars(s: str) -> str:
    """Remove control characters that are unsafe in plain text metadata."""
    return _INVALID_TEXT_CHARS.sub("", s)


@dataclass(frozen=True)
class SourceSection:
    """One normalized source section ready for text + metadata emission.

    Attributes
    ----------
    jurisdiction
        Full jurisdiction slug, e.g. ``"us-il"``, ``"us-federal"``, ``"uk"``.
    doc_type
        Singular document type: ``"statute"``, ``"regulation"``,
        ``"guidance"``, ``"manual"``.
    authority_code
        Short abbreviation of the authoritative citation format
        (``"ILCS"``, ``"RCW"``, ``"CFR"``, ``"USC"``, ``"NRS"``, etc.).
    work_number
        Identifier unique to this work within its jurisdiction.
    citation
        Human-readable citation text.
    heading
        Section heading or title.
    body
        Body text, with paragraphs separated by blank lines.
    author_id
        Stable id for the source publisher, e.g. ``"il-legislature"``.
    author_name
        Display name for the source publisher.
    author_url
        URL to the authoritative source.
    generation_date
        Date this scrape ran.
    """

    jurisdiction: str
    doc_type: str
    authority_code: str
    work_number: str
    citation: str
    heading: str
    body: str
    author_id: str
    author_name: str
    author_url: str
    generation_date: date


def render_source_text(section: SourceSection) -> str:
    """Render normalized body text for the ``.txt`` artifact."""
    paragraphs = split_paragraphs(strip_invalid_control_chars(section.body))
    return "\n\n".join(paragraphs) + ("\n" if paragraphs else "")


def render_source_metadata_yaml(section: SourceSection, *, text_path: str) -> str:
    """Render deterministic sidecar metadata for the ``.meta.yaml`` artifact."""
    return "\n".join(
        [
            "format: axiom-source-section/v1",
            f"jurisdiction: {_yaml_scalar(section.jurisdiction)}",
            f"doc_type: {_yaml_scalar(section.doc_type)}",
            f"authority_code: {_yaml_scalar(section.authority_code)}",
            f"work_number: {_yaml_scalar(section.work_number)}",
            f"citation: {_yaml_scalar(strip_invalid_control_chars(section.citation))}",
            f"heading: {_yaml_scalar(strip_invalid_control_chars(section.heading))}",
            f"generation_date: {_yaml_scalar(section.generation_date.isoformat())}",
            "source:",
            f"  author_id: {_yaml_scalar(section.author_id)}",
            f"  author_name: {_yaml_scalar(strip_invalid_control_chars(section.author_name))}",
            f"  author_url: {_yaml_scalar(section.author_url)}",
            f"text_path: {_yaml_scalar(text_path)}",
            "",
        ]
    )


def _yaml_scalar(value: str) -> str:
    """Return a YAML-safe scalar without adding a YAML dependency."""
    return json.dumps(value, ensure_ascii=False)
