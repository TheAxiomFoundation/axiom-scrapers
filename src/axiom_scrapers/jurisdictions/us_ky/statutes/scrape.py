"""Kentucky Revised Statutes (KRS) scraper.

Source — `apps.legislature.ky.gov`
----------------------------------
Kentucky serves statutes as PDFs (not HTML). The discovery path is:

1. Master TOC at ``chapter.aspx?Chapter=1`` (the query string is a
   quirk — the page actually lists every chapter). Anchors look like::

       <a class="chapter" href="chapter.aspx?id={cid}">
         CHAPTER {token} {heading}
       </a>

2. Chapter page at ``chapter.aspx?id={cid}`` lists sections::

       <a class="statute" href="statute.aspx?id={sid}">
         .010  Heading text here.
       </a>

3. ``statute.aspx?id={sid}`` returns a PDF. ``pdftotext -layout``
   yields clean text whose first line is
   ``{chapter}.{section}  {heading}.`` followed by body paragraphs,
   trailing in ``Effective:`` / ``History:`` metadata we strip.

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
from pathlib import Path

from axiom_scrapers._common import Scraper, SourceSection, clean_text, http_get

BASE = "https://apps.legislature.ky.gov/law/statutes"

_CHAPTER_ANCHOR = re.compile(
    r'<a[^>]*class="chapter"[^>]*href="chapter\.aspx\?id=(?P<cid>\d+)"[^>]*>'
    r"\s*CHAPTER\s+(?P<token>\S+)\s+(?P<heading>[^<]*?)\s*</a>",
    re.DOTALL | re.IGNORECASE,
)

_SECTION_ANCHOR = re.compile(
    r'<a[^>]*class="statute"[^>]*href="statute\.aspx\?id=(?P<sid>\d+)"[^>]*>'
    r"\s*(?P<sec>\.\S+)\s+(?P<heading>.*?)\s*</a>",
    re.DOTALL | re.IGNORECASE,
)

# Trailer lines that start the "publication history" block. Once we see
# one of these at the start of a line, everything from there to the end
# of the body is dropped.
_TRAILER_PREFIX = re.compile(
    r"^\s*(Effective:|History:|Legislative Research Commission Note|"
    r"Catchline at repeal:)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class KYSectionRef:
    """Handle for one KRS section — chapter + section_dot + section_id."""

    chapter: str  # e.g. "1", "6A", "141"
    section_dot: str  # e.g. ".010"
    section_id: str  # opaque KY database id
    toc_heading: str  # heading from TOC (fallback if PDF extraction fails)


class KRSStatutesScraper(Scraper[KYSectionRef]):
    """Scrape Kentucky Revised Statutes.

    ``SectionRef`` is a :class:`KYSectionRef`. Chapter TOC pages are
    fetched in :meth:`list_sections`; PDF fetch + text extraction
    happens per-section in :meth:`parse_section` (slow: ~1s each).
    """

    jurisdiction = "us-ky"
    doc_type = "statute"
    authority_code = "KRS"
    author_id = "ky-legislature"
    author_name = "Kentucky General Assembly"
    author_url = "https://legislature.ky.gov"
    workers = 4  # KY IIS is sometimes slow; keep pool small

    def list_sections(self) -> Iterable[KYSectionRef]:
        for chapter, cid in list_chapter_tokens_and_ids():
            for section_dot, sid, toc_heading in _list_chapter_sections(cid):
                yield KYSectionRef(chapter, section_dot, sid, toc_heading)

    def parse_section(self, ref: KYSectionRef) -> SourceSection | None:
        res = http_get(f"{BASE}/statute.aspx?id={ref.section_id}")
        if res is None:
            return None
        if not res.body.startswith(b"%PDF"):
            return None
        text = pdftotext(res.body)
        if text is None:
            return None
        heading, body = parse_pdf_body(text, ref.chapter, ref.section_dot)
        if not heading:
            heading = ref.toc_heading
        if not body:
            return None
        section = f"{ref.chapter}{ref.section_dot}"  # e.g. "1.010"
        return SourceSection(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=section,
            citation=f"KRS {section}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: SourceSection) -> Path:
        """``us-ky/statute/ch-{chapter}/ch-{chapter}-sec-{section}.txt``."""
        chapter = section.work_number.split(".", 1)[0]
        safe_section = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"ch-{chapter}",
            f"ch-{chapter}-sec-{safe_section}.txt",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def parse_pdf_body(text: str, chapter: str, section_dot: str) -> tuple[str, str]:
    """Extract ``(heading, body)`` from pdftotext output.

    The PDF's first non-empty line is ``{chapter}{section_dot} {heading}.``.
    Body is everything after, up to the first ``Effective:`` /
    ``History:`` / ``Legislative Research Commission Note`` line.

    Returns ``("", "")`` if the PDF produced no usable text.
    """
    lines = text.splitlines()
    heading = ""
    body_start = 0
    num_prefix = f"{chapter}{section_dot}"  # e.g. "1.010"
    for i, raw in enumerate(lines):
        ln = raw.strip()
        if not ln:
            continue
        if ln.startswith(num_prefix):
            heading = ln[len(num_prefix) :].strip()
        else:
            heading = ln
        heading = heading.rstrip(".").strip()
        body_start = i + 1
        break

    kept: list[str] = []
    for raw in lines[body_start:]:
        if _TRAILER_PREFIX.match(raw):
            break
        kept.append(raw)

    paragraphs: list[str] = []
    buf: list[str] = []
    for raw in kept:
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


def extract_chapter_anchors(html: str) -> list[tuple[str, str]]:
    """Return ``[(chapter_token, chapter_id), ...]`` from the master TOC."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for m in _CHAPTER_ANCHOR.finditer(html):
        token = m.group("token").strip().upper()
        cid = m.group("cid")
        if token in seen:
            continue
        seen.add(token)
        out.append((token, cid))
    return out


def extract_section_anchors(html: str) -> list[tuple[str, str, str]]:
    """Return ``[(section_dot, section_id, heading), ...]`` for a chapter."""
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for m in _SECTION_ANCHOR.finditer(html):
        sec = m.group("sec").strip()
        sid = m.group("sid")
        heading = clean_text(m.group("heading")).rstrip(".")
        if sid in seen:
            continue
        seen.add(sid)
        out.append((sec, sid, heading))
    return out


def pdftotext(data: bytes) -> str | None:
    """Run ``pdftotext -layout`` on a PDF byte blob.

    Returns ``None`` if ``pdftotext`` is not installed (so callers can
    soft-skip) or produces no output. Raises nothing — KY PDFs sometimes
    have corrupt trailers but pdftotext still prints usable stdout.
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


def list_chapter_tokens_and_ids() -> list[tuple[str, str]]:
    res = http_get(f"{BASE}/chapter.aspx?Chapter=1")
    if res is None:
        return []
    return extract_chapter_anchors(res.text())


def _list_chapter_sections(chapter_id: str) -> list[tuple[str, str, str]]:
    res = http_get(f"{BASE}/chapter.aspx?id={chapter_id}")
    if res is None:
        return []
    return extract_section_anchors(res.text())
