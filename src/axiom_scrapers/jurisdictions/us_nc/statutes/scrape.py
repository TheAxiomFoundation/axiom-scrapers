"""North Carolina General Statutes (G.S.) scraper.

Source — `ncleg.gov`
--------------------
Unlike Illinois, where each section is its own file, North Carolina
publishes each *chapter* as a single large HTML document::

    https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/ByChapter/Chapter_{N}.html

Chapter tokens are mostly numeric (``1``, ``2``, ``105``) with a few
alphabetic suffixes (``1A``, ``14B``) for chapters that were added
between existing numbers.  Sections within a chapter are marked by a
header paragraph of the form::

    <p><span>&sect; N-N. &nbsp;Heading.</span></p>

followed by one or more body paragraphs and ending with a history
parenthetical like ``(C.C.P., s. 1; Code, s. 125; ...)``.  Section
numbers can include dots (``1-339.1``) and colons — so the section
capture regex must be greedy across those characters.

Parse model
-----------
Because one fetch yields many sections, the scraper's
``list_sections`` fetches each chapter page once, splits it into
``(chapter, section)`` entries, caches them on the instance, and
yields the cache keys.  ``parse_section`` then reads from the cache
— no duplicate network calls — and returns a :class:`SourceSection` per
ref.  Threads in the base-class runner operate on the cache reads,
which is cheap and thread-safe because the cache is populated
serially inside ``list_sections`` before ``run`` starts the pool.

Output layout
-------------
``us-nc/statute/ch-{chapter}/ch-{chapter}-sec-{section}.txt`` —
nested by chapter token so the tree stays browseable.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from axiom_scrapers._common import Scraper, SourceSection, clean_text, http_get

BASE = "https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/ByChapter"
TOC_URL = "https://www.ncleg.gov/Laws/GeneralStatutesTOC"

# Section header: a <p> whose first <span> starts with "&sect; N-N. Heading.".
#
# NC emits the literal HTML entity ``&sect;`` (never ``§``). The section
# identifier is a greedy scan over digits / letters / ``.`` / ``-`` / ``:``
# so ``1-339.1`` captures as one unit. The delimiter between section id
# and heading is ``.\s+`` — a dot followed by whitespace — with optional
# ``&nbsp;`` non-breaking-space artifacts from the Word export.
_HEADER_RE = re.compile(
    r"<p[^>]*>\s*<span[^>]*>&sect;\s+"
    r"(?P<section>[0-9A-Za-z][0-9A-Za-z.\-:]*)\.\s+"
    r"(?:&nbsp;\s*)*(?P<heading>.*?)</span>",
    re.DOTALL | re.IGNORECASE,
)

# "Editor's Note" / cite-history trailers: a lone ``<p><span>(...)</span></p>``
# paragraph at the end of a section's slab, holding italicized parentheticals
# with no body text around them. Anchored to the end of the slab so mid-body
# parentheticals (part of the law itself) survive.
_TRAILER_RE = re.compile(
    r"<p[^>]*>\s*<span[^>]*>\s*\(.*?\)\s*</span>\s*</p>\s*$",
    re.DOTALL | re.IGNORECASE,
)

# Chapter-token link extractor for the TOC. Tokens are alphanumeric
# (``1``, ``1A``, ``105``). Order matters for display but not for scrape.
_CHAPTER_LINK_RE = re.compile(
    r"/EnactedLegislation/Statutes/HTML/ByChapter/Chapter_([A-Za-z0-9]+)\.html",
)


class GSStatutesScraper(Scraper[tuple[str, str]]):
    """Scrape NC General Statutes.

    ``SectionRef`` is ``(chapter_token, section_id)``.  Each chapter's
    HTML is fetched once during :meth:`list_sections`, split into
    sections, and the parsed ``(heading, body)`` tuples cached on
    ``self._cache``.  :meth:`parse_section` is a pure cache read —
    safe to call from any thread in the base runner's pool.
    """

    jurisdiction = "us-nc"
    doc_type = "statute"
    authority_code = "GS"
    author_id = "nc-legislature"
    author_name = "North Carolina General Assembly"
    author_url = "https://www.ncleg.gov"
    workers = 8

    def __init__(self, *, generation_date: date | None = None) -> None:
        super().__init__(generation_date=generation_date)
        # (chapter, section) -> (heading, body). Populated by list_sections.
        self._cache: dict[tuple[str, str], tuple[str, str]] = {}

    def list_sections(self) -> Iterable[tuple[str, str]]:
        """Fetch every chapter page, cache the parsed sections, yield refs."""
        for chapter in _list_chapter_tokens():
            html = _fetch_text(f"{BASE}/Chapter_{chapter}.html")
            if not html:
                continue
            for section_id, heading, body in split_sections(html):
                self._cache[(chapter, section_id)] = (heading, body)
                yield (chapter, section_id)

    def parse_section(self, ref: tuple[str, str]) -> SourceSection | None:
        """Build a :class:`SourceSection` from the cached parse.

        Returns ``None`` when the body is empty (repealed-section
        placeholder) so the base runner counts it as skipped.
        """
        _, section_id = ref
        cached = self._cache.get(ref)
        if cached is None:
            return None
        heading, body = cached
        if not body or body.lower() in {"repealed.", "repealed"}:
            return None
        return SourceSection(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=section_id,
            citation=f"G.S. {section_id}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: SourceSection) -> Path:
        """Nest by chapter so the tree stays browseable.

        NC section IDs already encode the chapter as the prefix before
        the first ``-`` (e.g. ``1-339.1`` is chapter 1, section 339.1;
        ``14B-500`` is chapter 14B, section 500), so we split once.
        """
        chapter = section.work_number.split("-", 1)[0]
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"ch-{chapter}",
            f"ch-{chapter}-sec-{section.work_number}.txt",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def split_sections(html: str) -> list[tuple[str, str, str]]:
    """Split a chapter's HTML into ``(section_id, heading, body)`` tuples.

    Strategy:

    * Scan for every ``<p><span>&sect; N-N. Heading.</span></p>`` header.
    * Body is the slab from one header's end to the next header's start
      (or end-of-file for the last section).
    * Strip the tailing cite-history / Editor's Note ``<p><span>(...)</span></p>``
      paragraph from the slab before normalizing to text.  NC tucks the
      original session-law citation there; we don't need it in the body.

    Returns an empty list when the document contains no section headers
    (e.g. a chapter that was fully repealed and is served as an
    annotation-only stub).
    """
    starts = list(_HEADER_RE.finditer(html))
    out: list[tuple[str, str, str]] = []
    for i, m in enumerate(starts):
        section = m.group("section")
        heading = clean_text(m.group("heading")).rstrip(".").strip()
        body_start = m.end()
        body_end = starts[i + 1].start() if i + 1 < len(starts) else len(html)
        slab = html[body_start:body_end]
        slab = _TRAILER_RE.sub("", slab)
        body = clean_text(slab)
        out.append((section, heading, body))
    return out


def _list_chapter_tokens() -> list[str]:
    """Return chapter tokens from the Table-of-Contents page.

    Sorts naturally: ``1, 1A, 2, 14B, 105`` — numeric part, then alpha
    suffix — so the run order matches how lawyers refer to the chapters.
    """
    html = _fetch_text(TOC_URL)
    tokens = set(_CHAPTER_LINK_RE.findall(html))
    return sorted(tokens, key=_chapter_sort_key)


def _chapter_sort_key(token: str) -> tuple[int, str]:
    m = re.match(r"(\d+)([A-Za-z]*)", token)
    if not m:
        return (10**9, token)
    return (int(m.group(1)), m.group(2))


def _fetch_text(url: str) -> str:
    """Fetch a URL; return empty string on failure."""
    res = http_get(url)
    return res.text() if res else ""
