"""Illinois Compiled Statutes (ILCS) scraper.

Source — `ilga.gov/ftp/ILCS/`
---------------------------
The Illinois General Assembly publishes the entire ILCS as a static
FTP-style tree under ``https://www.ilga.gov/ftp/ILCS/``::

    /ftp/ILCS/Ch {chapter:04d}/Act {act:04d}/{chapter:04d}{act:04d}0K{section}.html

Each section file is a tiny HTML document whose header encodes the
citation:

    (35 ILCS 155/2)            ← canonical citation
    (from Ch. 120, par. 1702)  ← pre-consolidation cross-reference
    Sec. 2.  Definitions.      ← section number + heading
    <body paragraphs …>
    (Source: P.A. 103-520, …)  ← public-act history, stripped

The tree walker is three deep (chapters → acts → section files);
:meth:`list_sections` returns the URL of each section file, and
:meth:`parse_section` pulls citation + heading + body out of the HTML.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date

from axiom_scrapers._common import FetchResult, Scraper, Section, clean_text, http_get
from axiom_scrapers._common.base import LogFn  # re-exported for typing

BASE = "https://www.ilga.gov/ftp/ILCS"

# "(35 ILCS 155/2)" — chapter, act, section. Letters permitted on act
# (for Ch. 28A-style ids) and on section (for subsection suffixes).
_ILCS_HEADER = re.compile(
    r"\(\s*(?P<chapter>\d+)\s+ILCS\s+(?P<act>\d+(?:[-.]\d+)?)\s*/\s*(?P<section>[^)\s]+)\s*\)"
)

# "Sec. 2." — the section id is repeated here; we match the first
# "Sec. X." after the ILCS citation line to locate the heading.
_SEC_MARKER = re.compile(r"Sec\.\s*(?P<section>[\w.\-]+?)\s*\.")

# Source-of-enactment marker; always at the end of the body text.
_SOURCE_TAIL = re.compile(r"\(\s*Source:[^)]*\)\s*$")


class ILCSStatutesScraper(Scraper[str]):
    """One scraper instance = one full ILCS scrape.

    ``SectionRef`` is the section-file URL (``str``); the subclass is
    parameterized on ``Scraper[str]`` so the base class's parallel
    runner can hand each URL to :meth:`parse_section`.
    """

    jurisdiction = "us-il"
    doc_type = "statute"
    authority_code = "ILCS"
    author_id = "il-legislature"
    author_name = "Illinois General Assembly"
    author_url = "https://www.ilga.gov"
    workers = 8

    def list_sections(self) -> Iterable[str]:
        """Walk chapters → acts → section files, yielding section URLs."""
        for chapter_href in _list_chapter_hrefs():
            for act_href in _list_act_hrefs(chapter_href):
                yield from _list_section_urls(act_href)

    def parse_section(self, url: str) -> Section | None:
        res = http_get(url)
        if res is None:
            return None
        return _parse_section_html(
            html=res.text(),
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: Section) -> "import_pathlib.Path":  # type: ignore[name-defined]
        """Nest by chapter so the tree stays browseable."""
        from pathlib import Path  # local — keep the module import surface tight

        chapter = section.work_number.split("-")[0]
        return Path(
            self.jurisdiction,
            self.doc_type,
            f"ch-{chapter}",
            f"{section.work_number}.xml",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def _parse_section_html(html: str, generation_date: date) -> Section | None:
    """Turn one section-file HTML body into a :class:`Section`, or None.

    Parse strategy:

    * Section header "(N ILCS A/S)" locates the section's identity.
    * "Sec. N." marks the boundary between citation chunk and content.
    * The *next* span of visible text after "Sec. N.", up to the first
      period, is the heading (e.g. "Definitions"). ILCS wraps this in
      a separate ``<code><font>...</font></code>`` span from the body
      lead-in, so scanning the first cleaned sentence does the right
      thing.
    * Everything after that first period is body text. The
      "(Source: …)" public-act history trailer is stripped.

    Returns ``None`` if the ILCS header isn't present or the body is
    effectively empty (repealed-section placeholders render as just
    "Sec. N. Repealed." with no further content).
    """
    m = _ILCS_HEADER.search(html)
    if not m:
        return None
    chapter = m.group("chapter")
    act = m.group("act")
    section = m.group("section")

    after_header = html[m.end() :]
    sec_m = _SEC_MARKER.search(after_header)
    heading = ""
    body_start = after_header
    if sec_m:
        tail = after_header[sec_m.end() :]
        # Heading = first sentence, which terminates at a period
        # followed by whitespace. Falls back to the pre-<br> chunk if
        # no period is present (rare edge cases).
        cleaned = clean_text(tail)
        period = re.search(r"\.(?=\s)", cleaned)
        if period:
            heading = cleaned[: period.start()].strip()
            body_text = cleaned[period.end() :].strip()
        else:
            br_m = re.search(r"<br", tail, re.IGNORECASE)
            heading_raw = tail[: br_m.start()] if br_m else tail[:200]
            heading = clean_text(heading_raw).rstrip(".").strip()
            body_text = clean_text(tail[br_m.end() :] if br_m else tail)
        body = body_text
    else:
        body = clean_text(body_start)

    body = _SOURCE_TAIL.sub("", body).strip()
    # Lone "Repealed" placeholders fall through here as short bodies —
    # guard explicitly so they're treated as no-body.
    if not body or body.lower() in {"repealed.", "repealed"}:
        return None

    work_number = f"{chapter}-{act}-{section}"
    citation = f"{chapter} ILCS {act}/{section}"
    return Section(
        jurisdiction="us-il",
        doc_type="statute",
        authority_code="ILCS",
        work_number=work_number,
        citation=citation,
        heading=heading,
        body=body,
        author_id="il-legislature",
        author_name="Illinois General Assembly",
        author_url="https://www.ilga.gov",
        generation_date=generation_date,
    )


def _list_chapter_hrefs() -> list[str]:
    """Return the FTP-dir HREFs for every ILCS chapter (``/ftp/ILCS/Ch%200005/`` etc.)."""
    html = _fetch_text(f"{BASE}/")
    return [
        href
        for href, label, is_dir in _parse_iis_listing(html)
        if is_dir and re.match(r"Ch\s+\d+", label)
    ]


def _list_act_hrefs(chapter_href: str) -> list[str]:
    html = _fetch_text(f"https://www.ilga.gov{chapter_href}")
    return [
        href
        for href, label, is_dir in _parse_iis_listing(html)
        if is_dir and re.match(r"Act\s+\d+", label)
    ]


def _list_section_urls(act_href: str) -> list[str]:
    html = _fetch_text(f"https://www.ilga.gov{act_href}")
    urls: list[str] = []
    for href, label, is_dir in _parse_iis_listing(html):
        if is_dir:
            continue
        if re.match(r"^\d{9}K[\w.\-]+\.html$", label):
            urls.append(f"https://www.ilga.gov{href}")
    return urls


def _fetch_text(url: str) -> str:
    """Fetch a URL and return text, returning ``""`` on failure."""
    res = http_get(url)
    return res.text() if res else ""


def _parse_iis_listing(html: str) -> list[tuple[str, str, bool]]:
    """Parse IIS-style directory listing; return ``(href, label, is_dir)`` tuples.

    Skips the ``[To Parent Directory]`` self-link.
    """
    out: list[tuple[str, str, bool]] = []
    for m in re.finditer(r'<A HREF="([^"]+)">([^<]+)</A>', html):
        href, label = m.group(1), m.group(2)
        if label.startswith("["):
            continue
        out.append((href, label, href.endswith("/")))
    return out
