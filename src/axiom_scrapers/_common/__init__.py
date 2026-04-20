"""Shared infrastructure for every scraper.

Most per-state scrapers import from here:

* :mod:`.http` — polite URL fetcher with retries.
* :mod:`.text` — HTML/whitespace normalization.
* :mod:`.akn` — Akoma Ntoso 3.0 XML builder.
* :mod:`.base` — :class:`Scraper` abstract base class; subclass to add
  a new jurisdiction.
"""

from .akn import AKN_NS, Section, build_akn_xml
from .base import ScrapeResult, Scraper
from .http import DEFAULT_UA, FetchResult, http_get
from .text import clean_text, safe_path_segment, split_paragraphs

__all__ = [
    "AKN_NS",
    "DEFAULT_UA",
    "FetchResult",
    "ScrapeResult",
    "Scraper",
    "Section",
    "build_akn_xml",
    "clean_text",
    "http_get",
    "safe_path_segment",
    "split_paragraphs",
]
