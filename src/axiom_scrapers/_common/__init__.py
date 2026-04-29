"""Shared infrastructure for every scraper.

Most per-state scrapers import from here:

* :mod:`.http` — polite URL fetcher with retries.
* :mod:`.text` — HTML/whitespace normalization.
* :mod:`.source_section` — normalized source-section artifacts.
* :mod:`.base` — :class:`Scraper` abstract base class; subclass to add
  a new jurisdiction.
"""

from .base import Scraper, ScrapeResult
from .http import DEFAULT_UA, FetchResult, http_get
from .source_section import (
    SourceSection,
    render_source_metadata_yaml,
    render_source_text,
    strip_invalid_control_chars,
)
from .text import clean_paragraphs, clean_text, safe_path_segment, split_paragraphs

__all__ = [
    "DEFAULT_UA",
    "FetchResult",
    "ScrapeResult",
    "Scraper",
    "SourceSection",
    "clean_paragraphs",
    "clean_text",
    "http_get",
    "render_source_metadata_yaml",
    "render_source_text",
    "safe_path_segment",
    "split_paragraphs",
    "strip_invalid_control_chars",
]
