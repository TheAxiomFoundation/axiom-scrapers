"""Arizona Revised Statutes (A.R.S.) scraper.

Source — `azleg.gov`
--------------------
Each title's table-of-contents lives at
``https://www.azleg.gov/arsDetail?title={title}``.  The TOC lists every
section as a ``/viewdocument/?docName=...`` wrapper link; the real
section file is the embedded ``https://www.azleg.gov/ars/{title}/{N}.htm``
URL.

Each section page is a small static HTML document shaped like::

    <p><font color=GREEN>1-101</font>. <font color=PURPLE><u>Heading</u></font></p>
    <p>Body paragraph one...</p>
    <p>Body paragraph two...</p>

Section ids are ``{title}-{section}`` (e.g. ``1-101``, ``43-1001``) —
the hyphen separator is part of the canonical citation so we preserve
it in ``work_number``.

Repealed sections return HTTP 307 (redirect to a "section not found"
page) which :func:`http_get` converts to ``None`` so they're skipped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from axiom_scrapers._common import Scraper, Section, clean_text, http_get

BASE = "https://www.azleg.gov"

# The TOC page wraps each section in ``/viewdocument/?docName=<real-url>``.
# Some links appear directly; we handle both forms. Title appears in the
# path so we can validate we've got the right one.
_TOC_LINK_RE = re.compile(
    r'href="(?:[^"]*?docName=)?(https?://www\.azleg\.gov/ars/(\d+)/(\d+)\.htm)"',
    re.IGNORECASE,
)

# Section header pattern: two sibling <font> spans, green for the
# section id, purple+underline for the heading. Attributes may or may
# not be quoted depending on which section — AZ's HTML is inconsistent.
_HEADER_RE = re.compile(
    r'<font\s+color\s*=\s*"?GREEN"?\s*>\s*(?P<section>[\w.\-]+)\s*</font>\s*\.?\s*'
    r'<font\s+color\s*=\s*"?PURPLE"?\s*>\s*<u>\s*(?P<heading>.*?)\s*</u>\s*</font>',
    re.DOTALL | re.IGNORECASE,
)


class ARSStatutesScraper(Scraper[tuple[int, str]]):
    """Scrape A.R.S.

    ``SectionRef`` is ``(title, url)`` — the title number is stored
    alongside the URL so :meth:`relative_output_path` can nest output
    without re-parsing the URL path.
    """

    jurisdiction = "us-az"
    doc_type = "statute"
    authority_code = "ARS"
    author_id = "az-legislature"
    author_name = "Arizona State Legislature"
    author_url = "https://www.azleg.gov"
    workers = 4  # AZ 429s aggressively; keep the pool small

    # AZ titles run 1-49 with a couple of gaps (title 2, 37). The walker
    # does not hard-code gaps; it lists whatever the TOC emits per title
    # and silently drops empty titles.
    _TITLES = tuple(range(1, 50))

    def list_sections(self) -> Iterable[tuple[int, str]]:
        for title in self._TITLES:
            for url in _list_section_urls_for_title(title):
                yield (title, url)

    def parse_section(self, ref: tuple[int, str]) -> Section | None:
        _title, url = ref
        res = http_get(url)
        if res is None:
            return None
        parsed = parse_section_html(res.text())
        if parsed is None:
            return None
        section_id, heading, body = parsed
        if not body:
            return None
        return Section(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=section_id,
            citation=f"A.R.S. § {section_id}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: Section) -> Path:
        """Nest by title: ``us-az/statute/ch-{title}/ch-{title}-sec-{id}.xml``.

        A.R.S. section ids encode the title as the prefix before the
        first ``-`` (``1-101`` → title 1, ``43-1001`` → title 43), so
        we split once.
        """
        title = section.work_number.split("-", 1)[0]
        safe_section = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self.doc_type,
            f"ch-{title}",
            f"ch-{title}-sec-{safe_section}.xml",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def parse_section_html(html: str) -> tuple[str, str, str] | None:
    """Extract ``(section_id, heading, body)`` from one section page.

    Returns ``None`` when the header line cannot be matched (repealed
    placeholders with no body, or error pages served in place of a
    section). The body is everything after the header up to the
    closing ``</BODY>`` tag, cleaned of HTML.
    """
    m = _HEADER_RE.search(html)
    if not m:
        return None
    section = m.group("section").strip()
    heading = clean_text(m.group("heading"))
    body_html = html[m.end() :]
    body_html = re.sub(r"</body>.*", "", body_html, flags=re.IGNORECASE | re.DOTALL)
    body = clean_text(body_html).lstrip(".").strip()
    return section, heading, body


def extract_toc_urls(html: str, title: int) -> list[str]:
    """Pull section-file URLs for ``title`` out of its TOC page."""
    urls: set[str] = set()
    for m in _TOC_LINK_RE.finditer(html):
        url, toc_title, _ = m.group(1), m.group(2), m.group(3)
        if int(toc_title) == title:
            urls.add(url)
    return sorted(urls)


def _list_section_urls_for_title(title: int) -> list[str]:
    res = http_get(f"{BASE}/arsDetail?title={title}")
    if res is None:
        return []
    return extract_toc_urls(res.text(), title)
