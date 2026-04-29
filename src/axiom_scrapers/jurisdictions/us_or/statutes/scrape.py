"""Oregon Revised Statutes (ORS) scraper.

Source — `oregonlegislature.gov/bills_laws/ors`
-----------------------------------------------
ORS is published one HTML page per chapter::

    https://www.oregonlegislature.gov/bills_laws/ors/ors{NNN}.html

where ``NNN`` is the zero-padded chapter token (``001`` for chapter 1,
``285A`` for chapter 285A). Chapter numbering has gaps; 404 is
expected for unused numbers and treated as "no content".

The file is Word-exported HTML (Windows-1252 encoded) with sections
marked by a bold lead-in::

    <b><span style='font-family:"Times New Roman",serif'>
        &nbsp;&nbsp; 1.020 Contempt of court.
    </span></b>
    <span>...body paragraphs...</span>

Bodies end at the next bold lead-in (another section or an
annotation block like ``Note:`` / ``Sec. N.``). Trailing
bracketed session-law citations (``[1981 c.1 §4; ...]``) are stripped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from axiom_scrapers._common import Scraper, SourceSection, http_get

BASE = "https://www.oregonlegislature.gov/bills_laws/ors"

_MAX_CHAPTER_NUM = 840
_LETTER_SUFFIXES = ("", "A", "B", "C", "D", "E")
_OR_ENCODING = "cp1252"

_SECTION_HEAD_RE = re.compile(
    r"<b>\s*<span[^>]*>\s*[\xa0\s]*"
    r"(?P<section>\d+[A-Za-z]?\.\d+[A-Za-z]?)"
    r"\s+(?P<heading>.*?)"
    r"</span>\s*</b>",
    re.DOTALL,
)

_ANY_LEADIN_RE = re.compile(
    r"<b>\s*<span[^>]*>\s*[\xa0\s]*"
    r"(?:"
    r"(?:\d+[A-Za-z]?\.\d+[A-Za-z]?\s)|"  # another section
    r"Note:|"
    r"Sec\.\s*\d+[A-Za-z]?\."  # "Sec. 3." historical annotation
    r")",
    re.DOTALL,
)


class ORSStatutesScraper(Scraper[tuple[str, str]]):
    """Scrape the Oregon Revised Statutes.

    ``SectionRef`` is ``(chapter_token, section)`` where ``chapter_token``
    is the dotless human form (``"1"``, ``"285A"``) and ``section`` is
    the full ``{chapter}.{sub}`` id. Chapter HTML bundles are fetched
    once in :meth:`list_sections` and parsed sections cached on
    ``self._cache`` for thread-safe :meth:`parse_section` reads.
    """

    jurisdiction = "us-or"
    doc_type = "statute"
    authority_code = "ORS"
    author_id = "or-legislature"
    author_name = "Oregon Legislative Assembly"
    author_url = "https://www.oregonlegislature.gov"
    workers = 4

    def __init__(self, *, generation_date: date | None = None) -> None:
        super().__init__(generation_date=generation_date)
        # (chapter_token, section) -> (heading, body)
        self._cache: dict[tuple[str, str], tuple[str, str]] = {}

    def list_sections(self) -> Iterable[tuple[str, str]]:
        for filename in enumerate_chapter_files():
            chapter_token = _chapter_key(filename)
            res = http_get(f"{BASE}/{filename}")
            if res is None:
                continue
            html = res.text(_OR_ENCODING)
            if not html:
                continue
            for section, heading, body in split_sections(html):
                sec_chapter = _section_chapter_token(section)
                if sec_chapter != chapter_token:
                    continue  # guard against regex spillover
                if not body:
                    continue
                ref = (chapter_token, section)
                self._cache[ref] = (heading, body)
                yield ref

    def parse_section(self, ref: tuple[str, str]) -> SourceSection | None:
        cached = self._cache.get(ref)
        if cached is None:
            return None
        heading, body = cached
        _chapter, section = ref
        return SourceSection(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=section,
            citation=f"ORS {section}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: SourceSection) -> Path:
        chapter = _section_chapter_token(section.work_number)
        safe_section = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"ch-{chapter}",
            f"ch-{chapter}-sec-{safe_section}.txt",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def split_sections(html: str) -> list[tuple[str, str, str]]:
    """Return ``(section, heading, body)`` tuples from a chapter bundle."""
    heads = list(_SECTION_HEAD_RE.finditer(html))
    terminator_positions = [m.start() for m in _ANY_LEADIN_RE.finditer(html)]
    sections: list[tuple[str, str, str]] = []
    for m in heads:
        section_num = m.group("section")
        heading_raw = m.group("heading")
        start = m.end()
        body_end = len(html)
        for ts in terminator_positions:
            if ts > start:
                body_end = ts
                break
        body_html = html[start:body_end]
        body = _clean_or(body_html).lstrip(". \t\n").strip()
        body = re.sub(r"\s*\[[^\[\]]*\]\s*$", "", body).strip()
        heading = _clean_or(heading_raw)
        heading = re.sub(r"\s+", " ", heading).strip().rstrip(".")
        sections.append((section_num, heading, body))
    return sections


def enumerate_chapter_files() -> list[str]:
    """Candidate ``orsNNN{suf}.html`` filenames covering every legal token."""
    out: list[str] = []
    for n in range(1, _MAX_CHAPTER_NUM + 1):
        token = f"{n:03d}"
        for suf in _LETTER_SUFFIXES:
            out.append(f"ors{token}{suf}.html")
    return out


def _chapter_key(filename: str) -> str:
    """Extract the human chapter token from a filename.

    ``ors001.html`` → ``"1"``; ``ors285A.html`` → ``"285A"``.
    """
    m = re.match(r"ors(\d+)([A-Za-z]?)\.html$", filename)
    if not m:
        return filename
    digits, suffix = m.group(1), m.group(2)
    return f"{int(digits)}{suffix}"


def _section_chapter_token(section: str) -> str:
    """``"1.020"`` → ``"1"``; ``"285A.050"`` → ``"285A"``."""
    m = re.match(r"(\d+)([A-Za-z]?)", section)
    if not m:
        return section.split(".", 1)[0]
    return f"{int(m.group(1))}{m.group(2)}"


def _clean_or(s: str) -> str:
    """ORS-specific cleaning: preserve <br>/</p> as paragraph breaks.

    Word-exported HTML has arbitrary whitespace inside tags and between
    words. We convert <br> and </p> to a sentinel, strip remaining
    tags, collapse whitespace, then reinstate the paragraph break.
    Shared :func:`clean_text` collapses the sentinel back out, so we
    substitute this single-purpose cleaner.
    """
    import html as _stdhtml

    sentinel = "@@PARABREAK@@"
    s = re.sub(r"<br\s*/?>", sentinel, s, flags=re.IGNORECASE)
    s = re.sub(r"</p\s*>", sentinel, s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = _stdhtml.unescape(s).replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s)
    s = s.replace(sentinel, "\n\n")
    s = re.sub(r" *\n *", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()
