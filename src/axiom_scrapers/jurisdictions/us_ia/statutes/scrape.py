"""Iowa Code scraper.

Source — `legis.iowa.gov/law/iowaCode`
-------------------------------------
Three-level navigation on the Iowa Legislature site, then a per-section PDF:

1. Root TOC at ``/law/iowaCode`` lists 16 Roman-numeral titles. Anchors
   point at ``/law/iowaCode/chapters?title={I..XVI}&year=YYYY``.
2. Title TOC lists chapters as ``/law/iowaCode/sections?codeChapter={N}&year=YYYY``.
   Chapter tokens are ``{number}`` or ``{number}{alpha}`` (e.g. ``1``, ``38D``).
3. Chapter TOC lists sections as links to ``/docs/code/YYYY/{chapter}.{section}.pdf``.
   Section tokens are ``{chapter}.{section}`` where either part may carry an
   alpha suffix (``1.15A``, ``422.5A``). The bare ``{chapter}.pdf`` link at
   the top of each chapter page is the full chapter PDF — we filter it out.

Per-section PDF shape (``pdftotext -layout``):

    1                                      TITLE HEADING, §1.1


      1.1 State boundaries.
      The boundaries of the state are ...
      [C51, §1; R60, §1; ...]
      2009 Acts, ch 41, §1
        Referred to in §1.2


    {timestamp}                                  Iowa Code 2026, Section 1.1 (p, c)

Multi-page sections repeat a top-of-page running head (``§{section}, TITLE
HEADING                    {page_no}``) and the timestamp footer on every
page. We strip both so body paragraphs stay contiguous.

Provenance (``[C51, ...]``, ``YYYY Acts, ch ...``, ``Referred to in §...``)
is kept inline with the body — consistent with how other Axiom state
ingests treat session-law citations (cf. Virginia).

Dependencies
------------
Requires ``pdftotext`` from poppler (macOS: ``brew install poppler``).
When the binary is missing, :meth:`parse_section` soft-fails via the
base class.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from axiom_scrapers._common import Scraper, Section, http_get

BASE = "https://www.legis.iowa.gov"

# Root TOC anchors — ``/law/iowaCode/chapters?title={I..XVI}&year=YYYY``.
_TITLE_LINK_RE = re.compile(
    r"""href=['"]/law/iowaCode/chapters\?title=(?P<title>[IVX]+)&year=\d+['"]""",
    re.IGNORECASE,
)

# Title TOC anchors — ``/law/iowaCode/sections?codeChapter={token}&year=YYYY``.
# Chapter token = digits with optional trailing alpha (e.g. ``1``, ``38D``).
_CHAPTER_LINK_RE = re.compile(
    r"""href=['"]/law/iowaCode/sections\?codeChapter=(?P<chapter>[0-9]+[A-Z]*)&year=\d+['"]""",
    re.IGNORECASE,
)

# Chapter TOC PDF links — ``/docs/code/YYYY/{chapter}.{section}.pdf``. Both
# chapter and section may carry an alpha suffix. The bare ``{chapter}.pdf``
# link (the full-chapter download) is excluded by requiring a dot.
_SECTION_LINK_RE = re.compile(
    r"""href=['"]/docs/code/\d+/"""
    r"""(?P<chapter>[0-9]+[A-Z]*)\.(?P<section>[0-9]+[A-Z]*)\.pdf['"]""",
    re.IGNORECASE,
)

# Running-head matchers (first line of each page). Page 1 leads with the
# chapter number; subsequent pages lead with ``§{section},``. The title
# text between the chapter token and ``§section`` is always all-uppercase
# (with punctuation / spaces / digits) — that's how we distinguish the
# header from a body line like ``2009 Acts, ch 41, §1`` that otherwise
# matches the shape ``{digits} ... §1``.
_PAGE1_HEADER_RE = re.compile(
    r"^\s*[0-9]+[A-Z]*\s{2,}[A-Z0-9 ,.\-&\u2013\u2014]+,\s*§\S+\s*$",
)
_PAGEN_HEADER_RE = re.compile(
    r"^\s*§\S+,\s+[A-Z0-9 ,.\-&\u2013\u2014]+\s+\d+\s*$",
)

# Page footer: ``{timestamp}          Iowa Code YYYY, Section X.X (pages, cols)``.
_PAGE_FOOTER_RE = re.compile(
    r"^.*Iowa Code \d+,\s*Section\s+\S+\s*\(\d+,\s*\d+\)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IASectionRef:
    """Handle for one Iowa Code section — chapter + section, plus the
    edition year so the PDF URL is stable across scrape runs."""

    chapter: str  # e.g. "1", "38D", "422"
    section: str  # e.g. "1", "15A", "7"
    year: int  # publication year, e.g. 2026

    @property
    def work_number(self) -> str:
        """``{chapter}.{section}`` — the Iowa Code citation number."""
        return f"{self.chapter}.{self.section}"


class IowaCodeStatutesScraper(Scraper[IASectionRef]):
    """Scrape the Iowa Code.

    Per-section PDF walker. Each PDF is one Iowa Code section; we extract
    text with ``pdftotext -layout`` and drop the running head + footer so
    the parsed body matches what Axiom ingests for other states.
    """

    jurisdiction = "us-ia"
    doc_type = "statute"
    authority_code = "Iowa Code"
    author_id = "ia-legislature"
    author_name = "Iowa General Assembly"
    author_url = "https://www.legis.iowa.gov"
    workers = 4  # legis.iowa.gov is slow on burst; keep the pool modest

    def __init__(self, *, generation_date: date | None = None, year: int | None = None) -> None:
        super().__init__(generation_date=generation_date)
        # Iowa publishes one Code edition per year; default to the
        # scrape year so the PDFs we fetch match the generation date.
        self.year = year or self.generation_date.year

    def list_sections(self) -> Iterable[IASectionRef]:
        for title in _list_titles(self.year):
            for chapter in _list_chapters(title, self.year):
                for section in _list_chapter_sections(chapter, self.year):
                    yield IASectionRef(chapter=chapter, section=section, year=self.year)

    def parse_section(self, ref: IASectionRef) -> Section | None:
        url = f"{BASE}/docs/code/{ref.year}/{ref.work_number}.pdf"
        res = http_get(url)
        if res is None:
            return None
        if not res.body.startswith(b"%PDF"):
            return None
        text = pdftotext(res.body)
        if text is None:
            return None
        heading, body = parse_pdf_body(text, ref.work_number)
        if not body:
            return None
        return Section(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=ref.work_number,
            citation=f"Iowa Code § {ref.work_number}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: Section) -> Path:
        """``us-ia/statutes/ch-{chapter}/ch-{chapter}-sec-{work_number}.xml``.

        The chapter prefix comes from splitting on the first dot —
        ``422.7`` → chapter ``422``, ``38D.2`` → chapter ``38D``.
        """
        chapter = section.work_number.split(".", 1)[0]
        safe_section = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"ch-{chapter}",
            f"ch-{chapter}-sec-{safe_section}.xml",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def strip_page_chrome(text: str) -> str:
    """Remove the running head + footer on each page of Iowa Code PDFs.

    ``pdftotext -layout`` preserves one top-of-page running head and one
    bottom timestamp line per page. Stripping them yields contiguous
    body paragraphs for multi-page sections like ``422.7``.
    """
    kept: list[str] = []
    for raw in text.splitlines():
        if _PAGE1_HEADER_RE.match(raw):
            continue
        if _PAGEN_HEADER_RE.match(raw):
            continue
        if _PAGE_FOOTER_RE.match(raw):
            continue
        kept.append(raw)
    return "\n".join(kept)


def parse_pdf_body(text: str, work_number: str) -> tuple[str, str]:
    """Extract ``(heading, body)`` from pdftotext output.

    The first non-empty line after page chrome is stripped looks like
    ``{work_number} {heading}.``. Body follows, with paragraph breaks
    preserved. Layout-preserving line wraps inside a paragraph collapse
    to a single space.

    Returns ``("", "")`` if the PDF produced no usable text.
    """
    cleaned = strip_page_chrome(text)
    lines = cleaned.splitlines()

    heading = ""
    body_start = 0
    prefix = work_number + " "  # e.g. "1.1 ", "422.7 "
    for i, raw in enumerate(lines):
        ln = raw.strip()
        if not ln:
            continue
        if ln.startswith(prefix):
            heading = ln[len(prefix) :].strip()
        elif ln.startswith(work_number):
            # Section id only, heading on the next non-empty line.
            heading = ""
            body_start = i + 1
            break
        else:
            heading = ln
        heading = heading.rstrip(".").strip()
        body_start = i + 1
        break

    paragraphs: list[str] = []
    buf: list[str] = []
    for raw in lines[body_start:]:
        stripped = raw.strip()
        if stripped:
            buf.append(stripped)
        elif buf:
            paragraphs.append(" ".join(buf))
            buf = []
    if buf:
        paragraphs.append(" ".join(buf))
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in paragraphs]
    paragraphs = [p for p in paragraphs if p]
    body = "\n\n".join(paragraphs)
    return heading, body


def extract_title_tokens(html: str) -> list[str]:
    """Return Roman-numeral title tokens (``I``, ``II``, …, ``XVI``) from the root TOC."""
    seen: dict[str, None] = {}
    for m in _TITLE_LINK_RE.finditer(html):
        seen[m.group("title").upper()] = None
    return list(seen)


def extract_chapter_tokens(html: str) -> list[str]:
    """Return chapter tokens (e.g. ``1``, ``38D``, ``422``) from a title TOC."""
    seen: dict[str, None] = {}
    for m in _CHAPTER_LINK_RE.finditer(html):
        seen[m.group("chapter").upper()] = None
    return list(seen)


def extract_section_tokens(html: str, chapter: str) -> list[str]:
    """Return section tokens (e.g. ``1``, ``15A``, ``5A``) for one chapter.

    The Iowa chapter page also shows the full-chapter PDF at
    ``{chapter}.pdf`` — that link has no section number so it's already
    excluded by the regex. Links for other chapters (if any) are filtered
    by ``chapter``.
    """
    target = chapter.upper()
    seen: dict[str, None] = {}
    for m in _SECTION_LINK_RE.finditer(html):
        if m.group("chapter").upper() != target:
            continue
        seen[m.group("section").upper()] = None
    return list(seen)


def pdftotext(data: bytes) -> str | None:
    """Run ``pdftotext -layout`` on a PDF byte blob.

    Returns ``None`` if ``pdftotext`` is not installed (so callers can
    soft-skip) or produces no output.
    """
    if shutil.which("pdftotext") is None:
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        try:
            proc = subprocess.run(
                ["pdftotext", "-layout", tmp.name, "-"],
                check=False,
                capture_output=True,
                timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
    if proc.returncode != 0 and not proc.stdout:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def _list_titles(year: int) -> list[str]:
    res = http_get(f"{BASE}/law/iowaCode")
    if res is None:
        return []
    tokens = extract_title_tokens(res.text())
    # The root page embeds anchors with a hardcoded year (the current
    # edition). If we're scraping a different edition the tokens are
    # still the same — Iowa titles are stable I..XVI.
    _ = year
    return tokens


def _list_chapters(title: str, year: int) -> list[str]:
    res = http_get(f"{BASE}/law/iowaCode/chapters?title={title}&year={year}")
    if res is None:
        return []
    return extract_chapter_tokens(res.text())


def _list_chapter_sections(chapter: str, year: int) -> list[str]:
    res = http_get(f"{BASE}/law/iowaCode/sections?codeChapter={chapter}&year={year}")
    if res is None:
        return []
    return extract_section_tokens(res.text(), chapter)
