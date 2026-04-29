"""New Mexico Statutes Annotated 1978 scraper.

Source — `nmonesource.com` (New Mexico Compilation Commission / Lexum)
---------------------------------------------------------------------
New Mexico does not publish its statutes as per-section HTML. The
official publisher (the New Mexico Compilation Commission) delivers
the full NMSA 1978 through the nmonesource.com Lexum Decisia portal
as one PDF per chapter — a single 0.5–8 MB PDF contains every section
in the chapter interleaved with annotations / history blocks.

Third-party HTML mirrors (law.justia.com, codes.findlaw.com) return
``403 Forbidden`` to non-interactive fetchers, so the PDF route is the
only machine-scrapable path.

Discovery is two HTTP hops:

1. ``/nmos/nmsa/en/nav_date.do?iframe=true`` (paginated ``?page=1..4``)
   lists every chapter as::

       <a class="decisia-browse-link" href="/nmos/nmsa/en/item/{id}/index.do">
         Chapter {chapter} - {title}
       </a>

   Chapter tokens are numeric (``1``, ``77``) or numeric + alpha
   (``24A``, ``46A``).

2. ``/nmos/nmsa/en/{id}/1/document.do`` returns the PDF for that
   chapter. ``pdftotext -layout`` yields a predictable shape:

       CHAPTER {chapter}
       {chapter title}
       ARTICLE {article}
       {article title}
       {chapter}-{article}-{section}. {heading}.
           {body paragraphs...}
       History: ...
                                   ANNOTATIONS
       {case law / compiler notes — NOT statutory text}
       {chapter}-{article}-{section+1}. ...

Section markers always sit at column 0 and match
``{chapter}-{article}-{section}\\.``. The body runs from the marker
line to the first ``History:`` line (or the next marker); the
``ANNOTATIONS`` block and everything below it is publisher
commentary, not statute, and is dropped.

Dependencies
------------
Requires ``pdftotext`` from poppler (macOS: ``brew install poppler``).
Missing binary → :meth:`list_sections` logs and yields nothing so the
run soft-fails.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from axiom_scrapers._common import Scraper, SourceSection, http_get

BASE = "https://nmonesource.com"
NAV_PATH = "/nmos/nmsa/en/nav_date.do?iframe=true"

# Chapter-list anchor. Chapter token is numeric with optional trailing
# letters (``24A``, ``59A``). Title runs until the closing ``</a>``.
_CHAPTER_ITEM_RE = re.compile(
    r"""<a[^>]+href=['"]/nmos/nmsa/en/item/(?P<item_id>\d+)/index\.do['"][^>]*>"""
    r"""\s*Chapter\s+(?P<chapter>[0-9]+[A-Za-z]*)\s*-\s*(?P<title>[^<]*?)\s*</a>""",
    re.DOTALL | re.IGNORECASE,
)

# Section marker anchored at line start. Chapter/article/section tokens
# follow NMSA convention: digits with optional trailing alpha for chapter
# and article, trailing dots allowed on section for subdivided ids
# (``1.1``, ``2A``). A trailing space is required so we don't match the
# bare citation shape ``17-1-2 NMSA 1978`` that appears inline in text.
_SECTION_MARKER_RE = re.compile(
    r"""^(?P<chapter>[0-9]+[A-Za-z]*)"""
    r"""-(?P<article>[0-9]+[A-Za-z]*)"""
    r"""-(?P<section>[0-9]+(?:\.[0-9]+)*[A-Za-z]*)"""
    r"""\.[ \t]+(?P<rest>\S.*)$""",
    re.MULTILINE,
)

# Any line starting one of these prefixes marks the end of statutory
# body for the current section. ``History:`` is the provenance block
# kept by the publisher; ``ANNOTATIONS`` introduces case-law
# commentary. Both are dropped.
_TRAILER_PREFIX = re.compile(
    r"""^\s*(History:|ANNOTATIONS\b)""",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NMSectionRef:
    """Handle for one NMSA section.

    Parameters
    ----------
    chapter
        Chapter token, e.g. ``"17"``, ``"24A"``.
    article
        Article token, e.g. ``"1"``, ``"2A"``.
    section
        Section token, e.g. ``"1"``, ``"1.1"``, ``"5A"``.
    """

    chapter: str
    article: str
    section: str

    @property
    def section_id(self) -> str:
        """Canonical NMSA section identifier, e.g. ``"17-1-1"``."""
        return f"{self.chapter}-{self.article}-{self.section}"


class NMSAStatutesScraper(Scraper[NMSectionRef]):
    """Scrape New Mexico Statutes Annotated 1978.

    Discovery fetches chapter TOC HTML once (4 pages); parse fetches
    each chapter PDF once, extracts every section in one pass, and
    hands :meth:`parse_section` a cached ``(heading, body)`` tuple so
    the hot path is a dictionary lookup.

    Parallelism is bounded to ``workers = 1`` because list_sections
    performs the PDF download + parse; letting multiple threads
    download 1–8 MB PDFs concurrently offered no benefit in testing
    and triggered spurious timeouts against the Lexum portal.
    """

    jurisdiction = "us-nm"
    doc_type = "statute"
    authority_code = "NMSA 1978"
    author_id = "nm-compilation-commission"
    author_name = "New Mexico Compilation Commission"
    author_url = "https://www.nmcompcomm.us"
    workers = 1

    def __init__(self, *, generation_date: date | None = None) -> None:
        super().__init__(generation_date=generation_date)
        # (chapter, article, section) -> (heading, body)
        self._cache: dict[NMSectionRef, tuple[str, str]] = {}

    def list_sections(self) -> Iterable[NMSectionRef]:
        if shutil.which("pdftotext") is None:
            print(
                "  WARN us-nm: pdftotext not installed; skipping run "
                "(install poppler: `brew install poppler`)",
                file=sys.stderr,
                flush=True,
            )
            return
        for item_id, chapter_token, _title in list_chapter_items():
            res = http_get(f"{BASE}/nmos/nmsa/en/{item_id}/1/document.do")
            if res is None or not res.body.startswith(b"%PDF"):
                continue
            text = pdftotext(res.body)
            if text is None:
                continue
            for ref, heading, body in parse_chapter_text(text, chapter_token):
                if not body:
                    continue
                self._cache[ref] = (heading, body)
                yield ref

    def parse_section(self, ref: NMSectionRef) -> SourceSection | None:
        data = self._cache.get(ref)
        if data is None:
            return None
        heading, body = data
        return SourceSection(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=ref.section_id,
            citation=f"NMSA 1978 § {ref.section_id}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: SourceSection) -> Path:
        """``us-nm/statutes/ch-{chapter}/ch-{chapter}-sec-{id}.txt``."""
        chapter = section.work_number.split("-", 1)[0]
        safe_section = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"ch-{chapter}",
            f"ch-{chapter}-sec-{safe_section}.txt",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def extract_chapter_items(html: str) -> list[tuple[str, str, str]]:
    """Return ``[(item_id, chapter_token, chapter_title), ...]``.

    Dedupes by ``item_id`` so rerendered nav blocks don't double-count.
    Chapter tokens are uppercased to canonicalize e.g. ``24a`` → ``24A``.
    """
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for m in _CHAPTER_ITEM_RE.finditer(html):
        item_id = m.group("item_id")
        if item_id in seen:
            continue
        seen.add(item_id)
        chapter = m.group("chapter").upper()
        title = re.sub(r"\s+", " ", m.group("title")).strip()
        out.append((item_id, chapter, title))
    return out


def parse_chapter_text(
    text: str,
    chapter_token: str,
) -> list[tuple[NMSectionRef, str, str]]:
    """Split one chapter's pdftotext output into per-section tuples.

    Only sections whose leading ``{chapter}`` matches ``chapter_token``
    are emitted — cross-references to other chapters that happen to
    appear at column 0 are ignored.

    Returns ``[(NMSectionRef, heading, body), ...]`` preserving the
    source order of the chapter.
    """
    target = chapter_token.upper()
    matches = list(_SECTION_MARKER_RE.finditer(text))
    out: list[tuple[NMSectionRef, str, str]] = []
    for i, m in enumerate(matches):
        chapter = m.group("chapter").upper()
        if chapter != target:
            continue
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw_block = text[m.start() : next_start]
        heading, body = _split_heading_body(m.group("rest"), raw_block[m.end() - m.start() :])
        ref = NMSectionRef(
            chapter=chapter,
            article=m.group("article").upper(),
            section=m.group("section"),
        )
        out.append((ref, heading, body))
    return out


def _split_heading_body(first_line_rest: str, remainder: str) -> tuple[str, str]:
    """Return ``(heading, body)`` from the tail of the marker line + following text.

    Heading rule
    ------------
    The marker line carries the heading up to the first top-level
    sentence-ending period — roughly ``{chapter}-{article}-{section}. {heading}.``.
    A trailing parenthetical like ``(Effective July 1, 2026.)`` may
    follow and is preserved as part of the heading when it fits on the
    same line; when the parenthetical wraps to the next line(s) the
    wrap is folded back in.

    Body rule
    ---------
    Body is every paragraph between heading and the first trailer
    line (``History:`` or ``ANNOTATIONS``). Blank lines separate
    paragraphs; intra-paragraph soft-wraps are rejoined with single
    spaces. All leading indentation from the PDF is stripped.
    """
    body_lines: list[str] = []
    remainder_lines = remainder.splitlines()

    # Fold a wrapped heading into a single line. NMSA catchlines wrap
    # when they're long or include an effective/repealed parenthetical
    # that overflows the column width. Pull subsequent lines into the
    # heading while any of these continuation markers is present:
    #   * an unclosed ``(`` or ``[`` bracket
    #   * a trailing ``;`` or ``,`` punctuation (mid-phrase)
    # Stop once brackets are balanced and the line ends in ``.`` / ``)`` / ``]``.
    joined = first_line_rest.strip()
    idx = 0
    while idx < len(remainder_lines):
        if not _heading_continues(joined):
            break
        joined = joined + " " + remainder_lines[idx].strip()
        idx += 1

    # Walk the remaining lines as body, stopping at the first trailer.
    seen_body = False
    for line in remainder_lines[idx:]:
        if _TRAILER_PREFIX.match(line):
            break
        if not line.strip():
            if seen_body:
                body_lines.append("")
            continue
        body_lines.append(line.strip())
        seen_body = True

    heading = _clean_heading(joined)
    body = _join_body(body_lines)
    return heading, body


def _heading_continues(partial: str) -> bool:
    """Return ``True`` if ``partial`` is an incomplete NMSA catchline.

    A heading is incomplete while any bracket is unbalanced or the
    last non-space character is a continuation mark (``;`` / ``,``).
    """
    stripped = partial.rstrip()
    if not stripped:
        return False
    if stripped.count("(") > stripped.count(")"):
        return True
    if stripped.count("[") > stripped.count("]"):
        return True
    return stripped[-1] in ";,"


def _clean_heading(text: str) -> str:
    """Strip the square-bracket convention and trailing period from a heading.

    NMSA 1978 uses ``[heading]`` brackets to flag editor-supplied
    catchlines on older sections; strip the brackets but keep the
    text. Trailing ``.`` is cosmetic and removed.
    """
    text = text.strip()
    # Older sections wrap heading in brackets — strip them.
    m = re.match(r"^\[(?P<core>[^\]]+)\](?P<rest>.*)$", text)
    if m:
        text = (m.group("core") + m.group("rest")).strip()
    # Strip the final period that always terminates NMSA catchlines.
    text = re.sub(r"\s+", " ", text).strip()
    if text.endswith("."):
        text = text[:-1].rstrip()
    return text


def _join_body(lines: list[str]) -> str:
    """Rejoin wrapped body lines into paragraph-separated text.

    Rules:
    * An empty string in ``lines`` marks a paragraph break.
    * Consecutive non-empty lines are folded with a single space so
      PDF soft-wraps disappear.
    * Runs of paragraph breaks collapse to a single blank line.
    """
    paragraphs: list[str] = []
    buf: list[str] = []
    for ln in lines:
        if ln == "":
            if buf:
                paragraphs.append(" ".join(buf))
                buf = []
        else:
            buf.append(ln)
    if buf:
        paragraphs.append(" ".join(buf))
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in paragraphs]
    paragraphs = [p for p in paragraphs if p]
    return "\n\n".join(paragraphs)


def pdftotext(data: bytes) -> str | None:
    """Run ``pdftotext -layout`` on a PDF byte blob, return stdout text.

    Returns ``None`` when ``pdftotext`` isn't installed, the invocation
    errored out with no output, or the process timed out (60 s).
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


def list_chapter_items() -> list[tuple[str, str, str]]:
    """Iterate the paginated nav listing, returning every chapter item.

    Walks ``nav_date.do?iframe=true&page=N`` until a page yields no
    new chapter rows. Stops at page 10 as a safety rail — NMSA 1978
    currently spans 4 pages and is unlikely to expand by a factor of 2.
    """
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for page in range(1, 11):
        url = f"{BASE}{NAV_PATH}" + (f"&page={page}" if page > 1 else "")
        res = http_get(url)
        if res is None:
            break
        page_items = extract_chapter_items(res.text())
        fresh = [item for item in page_items if item[0] not in seen]
        if not fresh:
            break
        for item in fresh:
            seen.add(item[0])
            out.append(item)
    return out
