"""North Dakota Century Code (NDCC) scraper.

Source — ``ndlegis.gov``
------------------------
North Dakota publishes the Century Code as one PDF per chapter at::

    https://ndlegis.gov/cencode/t{TT}c{CC}.pdf

and sometimes with alpha-numeric suffixes (``t04-1c01-1.pdf``) when a
title or chapter has been subdivided. The master TOC at
``/general-information/north-dakota-century-code`` lists every
section with a link that carries:

* a chapter-level PDF URL (``/cencode/t01c01.pdf``)
* a ``#nameddest=`` fragment (``1-01-01``, ``1-01-01p1``) that
  identifies the section *inside* the PDF
* the human-readable section number as link text (``1-01-01.1``)
* a heading cell in the adjacent ``<td>``

Each chapter PDF bundles all sections in that chapter. ``pdftotext
-layout`` yields text whose section boundaries look like::

    1-01-01. This act - How referred to.
    Body paragraphs follow, possibly continuing after a page break
    marked by a centered ``Page No. N`` line.

    1-01-01.1. Adoption of North Dakota Revised Code of 1943.
    Repealed by omission from this code.

Headings occasionally wrap to a second line before the trailing
period. Body paragraphs honor pdftotext's soft-wrap layout with
indented continuation lines.

Parse strategy
--------------
:meth:`list_sections` fetches the master TOC once, yields one
:class:`NDSectionRef` per anchor. :meth:`parse_section` fetches the
section's chapter PDF (cached instance-wide so each chapter PDF is
fetched at most once regardless of how many sections share it),
runs ``pdftotext``, and extracts just the requested section.

Dependencies
------------
Requires ``pdftotext`` from poppler (macOS: ``brew install poppler``;
homebrew on Apple Silicon installs to ``/opt/homebrew/bin/pdftotext``).
When the binary is missing, :func:`pdftotext` returns ``None`` and the
scraper soft-skips every section.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

from axiom_scrapers._common import Scraper, Section, clean_text, http_get

BASE = "https://ndlegis.gov"
TOC_URL = f"{BASE}/general-information/north-dakota-century-code"

# Matches one TOC row — chapter-level PDF href, nameddest fragment,
# section-number anchor text, heading text from the neighboring cell.
_TOC_ROW_RE = re.compile(
    r'<td\s+class="no-wrap">\s*'
    r'<a\s+href="(?P<pdf>/cencode/[^"#]+\.pdf)#nameddest=[^"]+">'
    r"\s*(?P<num>[0-9.]+-[0-9.]+-[0-9.]+)\s*</a>\s*</td>\s*"
    r"<td>(?P<heading>.*?)</td>",
    re.DOTALL | re.IGNORECASE,
)

# Matches a section header inside pdftotext output. A header is a
# line starting with ``<num>.`` followed by heading text on the same
# line. Requires non-whitespace heading text so bare end-of-sentence
# cross-references like "... as imposed by section 57-38-30." don't
# match. Each number part can carry an optional ``.<digits>`` decimal
# (e.g. ``16.1-08.1-03.15``).
_SECTION_HEADER_RE = re.compile(
    r"^[ \t]*"
    r"(?P<num>\d+(?:\.\d+)?-\d+(?:\.\d+)?-\d+(?:\.\d+)?)"
    r"\.[ \t]+(?P<heading>\S[^\n]*)$",
    re.MULTILINE,
)

_PAGE_MARKER_RE = re.compile(r"^\s*Page No\.\s*\d+\s*$")
_BANNER_RE = re.compile(r"^\s*(TITLE|CHAPTER)\s+[0-9.\-]+\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class NDSectionRef:
    """Handle for one NDCC section — section id + chapter-level PDF URL."""

    pdf_url: str  # ``/cencode/t01c01.pdf`` (path; joined with BASE on fetch)
    section_num: str  # e.g. ``1-01-01``, ``57-38-01.15``, ``16.1-08.1-03.15``
    toc_heading: str  # heading from the TOC (fallback if PDF parse fails)


class NDCCStatutesScraper(Scraper[NDSectionRef]):
    """Scrape the North Dakota Century Code.

    ``SectionRef`` is :class:`NDSectionRef`. The master TOC is fetched
    once in :meth:`list_sections`; chapter-level PDFs are fetched
    on-demand in :meth:`parse_section` and cached so multiple
    sections sharing a PDF trigger only one download + extraction.
    """

    jurisdiction = "us-nd"
    doc_type = "statute"
    authority_code = "NDCC"
    author_id = "nd-legislature"
    author_name = "North Dakota Legislative Assembly"
    author_url = "https://ndlegis.gov"
    workers = 4  # Gentle on ndlegis.gov; PDF extraction is CPU-bound anyway.

    def __init__(self, *, generation_date: date | None = None) -> None:
        super().__init__(generation_date=generation_date)
        # pdf_url -> {section_num: (heading, body)}. Populated lazily.
        self._chapter_cache: dict[str, dict[str, tuple[str, str]]] = {}
        self._cache_lock = threading.Lock()

    def list_sections(self) -> Iterable[NDSectionRef]:
        res = http_get(TOC_URL)
        if res is None:
            return
        for pdf_url, section_num, heading in extract_toc_entries(res.text()):
            yield NDSectionRef(pdf_url, section_num, heading)

    def parse_section(self, ref: NDSectionRef) -> Section | None:
        sections = self._get_chapter(ref.pdf_url)
        parsed = sections.get(ref.section_num)
        if parsed is not None:
            heading, body = parsed
        else:
            heading, body = ref.toc_heading, ""
        if not heading:
            heading = ref.toc_heading
        if not body:
            return None
        return Section(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=ref.section_num,
            citation=f"N.D. Cent. Code \u00a7 {ref.section_num}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: Section) -> Path:
        """``us-nd/statutes/ch-{title-chapter}/ch-{tc}-sec-{section}.xml``.

        ND section ids are ``{title}-{chapter}-{section}``; the first
        two parts form the chapter token (e.g. ``57-38`` in
        ``57-38-01.15``). Splitting on the second dash keeps siblings
        together so the tree stays browseable.
        """
        parts = section.work_number.split("-")
        chapter = "-".join(parts[:2]) if len(parts) >= 2 else parts[0]
        safe_section = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"ch-{chapter}",
            f"ch-{chapter}-sec-{safe_section}.xml",
        )

    # --- Chapter cache helpers ------------------------------------------

    def _get_chapter(self, pdf_url: str) -> dict[str, tuple[str, str]]:
        """Return parsed ``{section_num: (heading, body)}`` for a chapter.

        Threads may race on a first read; ``setdefault`` preserves the
        first inserter so redundant parses are discarded cheaply rather
        than requiring a per-URL lock.
        """
        cached = self._chapter_cache.get(pdf_url)
        if cached is not None:
            return cached
        parsed = self._fetch_and_parse_chapter(pdf_url)
        with self._cache_lock:
            return self._chapter_cache.setdefault(pdf_url, parsed)

    def _fetch_and_parse_chapter(
        self, pdf_url: str
    ) -> dict[str, tuple[str, str]]:
        res = http_get(urljoin(BASE, pdf_url))
        if res is None or not res.body.startswith(b"%PDF"):
            return {}
        text = pdftotext(res.body)
        if text is None:
            return {}
        return parse_chapter_pdf(text)


# --- Pure-function helpers (tested in isolation) -------------------------


def extract_toc_entries(html: str) -> list[tuple[str, str, str]]:
    """Return ``[(pdf_url, section_num, heading), ...]`` from the master TOC.

    Duplicates by section number are dropped — the TOC occasionally lists
    the same section twice under different display groupings.
    """
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for m in _TOC_ROW_RE.finditer(html):
        num = m.group("num").strip()
        if num in seen:
            continue
        seen.add(num)
        pdf_url = m.group("pdf")
        heading = clean_text(m.group("heading"))
        # ND's TOC uses the Unicode non-breaking hyphen (U+2011) between
        # words in headings. Normalize to ASCII hyphen so downstream
        # search/indexing behaves intuitively.
        heading = heading.replace("\u2011", "-")
        out.append((pdf_url, num, heading))
    return out


def parse_chapter_pdf(text: str) -> dict[str, tuple[str, str]]:
    """Split pdftotext output into ``{section_num: (heading, body)}``.

    Content before the first section header is treated as the
    chapter/title banner and dropped. Between headers we filter out
    ``Page No. N`` page markers and any repeated ``TITLE``/``CHAPTER``
    banners that some chapter PDFs print on every page.
    """
    matches = list(_SECTION_HEADER_RE.finditer(text))
    out: dict[str, tuple[str, str]] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        slab = text[m.end() : end]
        heading = _complete_heading(m.group("heading"), slab)
        body_start = _body_start_offset(m.group("heading"), slab)
        body = _clean_body(slab[body_start:])
        out[m.group("num")] = (heading, body)
    return out


def _complete_heading(first_line: str, slab: str) -> str:
    """Return the full heading, following wraps until a period.

    ``first_line`` is the captured heading from the header line. If it
    already ends with a period, it stands as the heading. Otherwise we
    consume subsequent non-blank lines (from ``slab``, which picks up
    right after the header match) until one ends with a period. The
    slab typically starts with a newline (the match ends at ``$``),
    so leading blanks are skipped rather than treated as a break.
    """
    parts = [first_line.strip()]
    if parts[0].endswith("."):
        return parts[0].rstrip(".").strip()
    for raw in slab.splitlines():
        stripped = raw.strip()
        if not stripped:
            if len(parts) > 1:
                break
            continue
        parts.append(stripped)
        if stripped.endswith("."):
            break
    joined = " ".join(parts).rstrip(".").strip()
    return re.sub(r"\s+", " ", joined)


def _body_start_offset(first_line: str, slab: str) -> int:
    """Bytes into ``slab`` at which body text begins (past the heading).

    When the heading fits on the header line, body starts at the first
    character of ``slab``. When it wraps, we advance past each wrap
    line so the body doesn't include duplicated heading fragments.
    The slab typically starts with a newline (the match ends at ``$``
    in MULTILINE mode); we advance past leading blank lines before
    looking for the period-terminated wrap line.
    """
    if first_line.strip().endswith("."):
        return 0
    offset = 0
    seen_content = False
    for raw in slab.splitlines(keepends=True):
        if not raw.strip():
            if seen_content:
                return offset
            offset += len(raw)
            continue
        seen_content = True
        offset += len(raw)
        if raw.strip().endswith("."):
            return offset
    return offset


def _clean_body(slab: str) -> str:
    """Strip page markers and banners, collapse wrapped lines to paragraphs.

    ``pdftotext -layout`` preserves pagination noise (centered ``Page
    No. N`` lines and occasional ``TITLE``/``CHAPTER`` banners on page
    tops). Drop those, then glue runs of non-blank lines into a single
    paragraph separated from other paragraphs by a blank line.
    """
    cleaned: list[str] = []
    for raw in slab.splitlines():
        if _PAGE_MARKER_RE.match(raw) or _BANNER_RE.match(raw):
            continue
        cleaned.append(raw)

    paragraphs: list[str] = []
    buf: list[str] = []
    for line in cleaned:
        stripped = line.strip()
        if stripped:
            buf.append(stripped)
        elif buf:
            paragraphs.append(" ".join(buf))
            buf = []
    if buf:
        paragraphs.append(" ".join(buf))
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in paragraphs]
    paragraphs = [p for p in paragraphs if p]
    return "\n\n".join(paragraphs)


def pdftotext(data: bytes) -> str | None:
    """Run ``pdftotext -layout`` on a PDF byte blob.

    Returns ``None`` if ``pdftotext`` is not installed (so callers can
    soft-skip) or produces no usable output.
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
                timeout=120,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
    if proc.returncode != 0 and not proc.stdout:
        return None
    return proc.stdout.decode("utf-8", errors="replace")
