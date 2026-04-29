"""Rhode Island General Laws (RIGL) scraper.

Source — `webserver.rilegislature.gov/Statutes`
-----------------------------------------------
LexisNexis-generated static site with four discovery layers:

1. Root ``/Statutes/`` lists titles (``TITLE1``, ``TITLE6A``,
   ``TITLE40.1``).
2. Each title index links to chapters via ``{title}-{chapter}/INDEX.htm``.
3. Each chapter index links to sections via
   ``{title}-{chapter}-{section}.htm``.
4. Each section page is ~4 KB and contains::

       <h3>R.I. Gen. Laws § 1-2-1</h3>
       <div>
         <p><b>§ 1-2-1. Heading.</b></p>
         <p><b>(a)</b>&nbsp;Body text…</p>
         <div><p>History of Section.<br>...</p></div>
       </div>

Repealed sections have only a ``History of Section.`` paragraph with
no substantive body; those are soft-skipped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from axiom_scrapers._common import Scraper, SourceSection, clean_text, http_get

BASE = "https://webserver.rilegislature.gov/Statutes"

# Tolerate §, &sect;, &#167;, &#xa7; — RIGL is inconsistent.
_SECT = r"(?:§|&sect;|&#167;|&#xa7;)"
_SPACE = r"(?:\s|&nbsp;|\xa0)+"

_CITATION_RE = re.compile(
    rf"<h3[^>]*>\s*R\.I\.\s*Gen\.\s*Laws\s*{_SECT}{_SPACE}"
    r"([0-9A-Za-z.\-]+)\s*</h3>",
    re.IGNORECASE,
)

_HEADER_RE = re.compile(
    rf"<p[^>]*>\s*<b>\s*{_SECT}{_SPACE}(?P<section>[0-9A-Za-z.\-]+)\s*\."
    rf"{_SPACE}?(?P<heading>.*?)\s*</b>\s*</p>",
    re.DOTALL | re.IGNORECASE,
)

_TITLE_LINK_RE = re.compile(
    r'href="TITLE([0-9A-Za-z.]+)/INDEX\.HTM"', re.IGNORECASE
)


@dataclass(frozen=True)
class RISectionRef:
    """Handle for one RIGL section — title + chapter + section id."""

    title: str  # e.g. "1", "6A", "40.1"
    chapter: str  # e.g. "2", "18.1"
    section: str  # e.g. "1", "17.1"


class RIGLStatutesScraper(Scraper[RISectionRef]):
    """Scrape the Rhode Island General Laws.

    Per-section URL walker using :class:`RISectionRef`. The base
    runner fetches each ~4 KB section page in parallel.
    """

    jurisdiction = "us-ri"
    doc_type = "statute"
    authority_code = "RIGL"
    author_id = "ri-legislature"
    author_name = "Rhode Island General Assembly"
    author_url = "https://webserver.rilegislature.gov"
    workers = 6

    def list_sections(self) -> Iterable[RISectionRef]:
        for title in _list_titles():
            for chapter in _list_chapter_tokens(title):
                for section in _list_section_tokens(title, chapter):
                    yield RISectionRef(title, chapter, section)

    def parse_section(self, ref: RISectionRef) -> SourceSection | None:
        url = (
            f"{BASE}/TITLE{ref.title}/{ref.title}-{ref.chapter}/"
            f"{ref.title}-{ref.chapter}-{ref.section}.htm"
        )
        res = http_get(url)
        if res is None:
            return None
        parsed = parse_section_page(res.text())
        if parsed is None:
            return None
        section_id, heading, body = parsed
        if not body:
            return None
        return SourceSection(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=section_id,
            citation=f"R.I. Gen. Laws \u00a7 {section_id}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: SourceSection) -> Path:
        """``us-ri/statute/ch-{title}/ch-{title}-sec-{section}.txt``.

        Title is the prefix before the first ``-`` in ``work_number``.
        """
        title = section.work_number.split("-", 1)[0]
        safe_section = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"ch-{title}",
            f"ch-{title}-sec-{safe_section}.txt",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def parse_section_page(html: str) -> tuple[str, str, str] | None:
    """Extract ``(section_id, heading, body)`` from a section page."""
    header = _HEADER_RE.search(html)
    body_start: int
    section: str
    heading: str
    if header is None:
        cit = _CITATION_RE.search(html)
        if cit is None:
            return None
        section = cit.group(1).strip()
        heading = ""
        body_start = cit.end()
    else:
        section = header.group("section").strip()
        heading = clean_text(header.group("heading")).rstrip(".")
        body_start = header.end()

    body_html = html[body_start:]
    body_html = re.sub(
        r"<div[^>]*>\s*<p[^>]*>\s*History of Section\..*?</body>",
        "</body>",
        body_html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    body_html = re.sub(
        r"<p[^>]*>\s*History of Section\..*?</p>",
        "",
        body_html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    body_html = re.sub(
        r"</body>.*", "", body_html, flags=re.IGNORECASE | re.DOTALL
    )
    body = clean_text(body_html).strip().strip("—–-").strip()
    return section, heading, body


def extract_titles(html: str) -> list[str]:
    """Return RI title tokens (``1``, ``6A``, ``40.1``) from a root index."""
    tokens = set(_TITLE_LINK_RE.findall(html))

    def key(t: str) -> tuple[int, str]:
        m = re.match(r"(\d+)(.*)", t)
        return (int(m.group(1)), m.group(2)) if m else (10**9, t)

    return sorted(tokens, key=key)


def extract_chapter_tokens(html: str, title: str) -> list[str]:
    """Return chapter tokens under ``title`` from a title index."""
    prefix = re.escape(title) + r"-"
    tokens = set(
        re.findall(
            rf'href="{prefix}([0-9A-Za-z.]+)/INDEX\.htm"', html, re.IGNORECASE
        )
    )

    def key(t: str) -> tuple[int, str]:
        m = re.match(r"(\d+)(.*)", t)
        return (int(m.group(1)), m.group(2)) if m else (10**9, t)

    return sorted(tokens, key=key)


def extract_section_tokens(html: str, title: str, chapter: str) -> list[str]:
    """Return section tokens under ``{title}-{chapter}`` from a chapter index."""
    prefix = re.escape(f"{title}-{chapter}-")
    tokens = set(
        re.findall(
            rf'href="{prefix}([0-9A-Za-z.]+)\.htm"', html, re.IGNORECASE
        )
    )

    def key(t: str) -> tuple[int, float]:
        m = re.match(r"(\d+)(?:\.(\d+))?(.*)", t)
        if not m:
            return (10**9, 0.0)
        base = int(m.group(1))
        dec = float(f"0.{m.group(2)}") if m.group(2) else 0.0
        return (base, dec)

    return sorted(tokens, key=key)


def _list_titles() -> list[str]:
    res = http_get(f"{BASE}/")
    if res is None:
        return []
    return extract_titles(res.text())


def _list_chapter_tokens(title: str) -> list[str]:
    res = http_get(f"{BASE}/TITLE{title}/INDEX.HTM")
    if res is None:
        return []
    return extract_chapter_tokens(res.text(), title)


def _list_section_tokens(title: str, chapter: str) -> list[str]:
    res = http_get(f"{BASE}/TITLE{title}/{title}-{chapter}/INDEX.htm")
    if res is None:
        return []
    return extract_section_tokens(res.text(), title, chapter)
