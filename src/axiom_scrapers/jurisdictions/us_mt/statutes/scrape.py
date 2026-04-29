"""Montana Code Annotated (MCA) scraper.

Source — ``mca.legmt.gov/bills/mca/``
------------------------------------
The MCA is served as a four-level static HTML tree::

    index.html                                   -> list of titles
    title_NNNN/chapters_index.html               -> list of chapters
    title_NNNN/chapter_NNNN/parts_index.html     -> list of parts
    title_NNNN/chapter_NNNN/part_NNNN/sections_index.html
                                                  -> list of sections
    title_NNNN/chapter_NNNN/part_NNNN/section_NNNN/
        NNNN-NNNN-NNNN-NNNN.html                 -> one section page

The four-digit directory tokens are zero-padded and scaled ×10 — the
trailing digit is a reserved slot left open for future subdivisions.
So ``title_0150`` -> title 15, ``chapter_0030`` -> chapter 3,
``part_0210`` -> part 21, ``section_0010`` -> section 1 within its
part. See :func:`_token_to_num`.

Each section page contains a single ``<div class="section-doc">`` with
one or more ``<p class="line-indent">`` blocks. The first paragraph
opens with::

    <span class="catchline">
      <span class="citation">15-30-2101</span>.&#8195;Definitions.
    </span>
    For the purpose of this chapter …

The citation span already encodes the canonical ``title-chapter-section``
number (with the Montana convention of packing part × 100 into the
section number — e.g. 15-30-**2101** = title 15, ch. 30, part 21,
section 1). Subsequent ``<p class="line-indent">`` blocks hold the
numbered body — ``(1)``, ``(a)``, etc. A separate
``<div class="history-doc">`` holds the session-law history; we drop it.

The historical domain ``archive.legmt.gov`` 301-redirects to
``mca.legmt.gov``; we use the live host directly to avoid the extra
hop (and to survive the archive endpoint periodically 404-ing after
redirect).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from axiom_scrapers._common import Scraper, SourceSection, clean_text, http_get

BASE = "https://mca.legmt.gov/bills/mca"

# Directory tokens look like ``title_0150`` / ``chapter_0030`` / etc.
# The trailing digit is reserved (always 0 for normal entries); divide
# by 10 to recover the canonical number.
_DIR_TOKEN = re.compile(r"_(\d+)$")

# Section index rows: one ``<a>`` wraps a section-dir href plus a
# ``<span class="citation">`` holding the dashed citation.
_SECTION_ROW = re.compile(
    r'<a[^>]+href="\.?/?(?P<dir>section_\d+)/(?P<file>[\d-]+\.html)"[^>]*>\s*'
    r'<span\s+class="citation"[^>]*>\s*(?P<cite>[^<]+?)\s*</span>',
    re.DOTALL,
)

# Section page body — only the <div class="section-doc">, dropping any
# trailing <div class="history-doc">.
_SECTION_DOC = re.compile(
    r'<div\s+class="section-doc"[^>]*>(?P<body>.*?)</div>\s*'
    r'(?=<div\s+class="history-doc"|</div>\s*<footer|</div>\s*$)',
    re.DOTALL,
)

# Catchline span inside section-doc: citation + heading.
_CATCHLINE = re.compile(
    r'<span\s+class="catchline"[^>]*>\s*'
    r'<span\s+class="citation"[^>]*>\s*(?P<cite>[^<]+?)\s*</span>'
    r'\s*\.?\s*(?P<heading>.*?)</span>',
    re.DOTALL,
)

# Paragraph blocks inside section-doc.
_PARA = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL | re.IGNORECASE)


class MCAStatutesScraper(Scraper[str]):
    """One scraper instance = one full MCA scrape.

    ``SectionRef`` is the section-page URL (``str``); the subclass is
    parameterized on ``Scraper[str]`` so the base class's parallel
    runner can hand each URL to :meth:`parse_section`.
    """

    jurisdiction = "us-mt"
    doc_type = "statute"
    authority_code = "MCA"
    author_id = "mt-legislature"
    author_name = "Montana Legislature"
    author_url = "https://leg.mt.gov"
    workers = 6

    def list_sections(self) -> Iterable[str]:
        """Walk titles -> chapters -> parts -> sections, yielding section URLs."""
        for title_dir in _list_titles():
            for chapter_dir in _list_chapters(title_dir):
                for part_dir in _list_parts(title_dir, chapter_dir):
                    yield from _list_section_urls(title_dir, chapter_dir, part_dir)

    def parse_section(self, url: str) -> SourceSection | None:
        res = http_get(url)
        if res is None:
            return None
        return _parse_section_html(res.text(), generation_date=self.generation_date)

    def relative_output_path(self, section: SourceSection) -> Path:
        """Nest by ``ch-{title}-{chapter}`` so the tree stays browseable.

        MCA citations are ``title-chapter-section`` (three dashes), so the
        first two dashed components identify the chapter grouping. We
        reuse them as the intermediate directory name.
        """
        parts = section.work_number.split("-")
        if len(parts) >= 2:
            chapter_key = f"ch-{parts[0]}-{parts[1]}"
        else:
            chapter_key = f"ch-{parts[0]}"
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            chapter_key,
            f"{section.work_number}.txt",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def _token_to_num(token: str) -> str:
    """Convert ``title_0150`` / ``chapter_0030`` / ``section_0010`` to its
    canonical number (``"15"`` / ``"3"`` / ``"1"``).

    The four-digit token is zero-padded and scaled ×10 (the trailing
    digit is a reserved slot for future subdivisions). Dividing by ten
    recovers the canonical number. Tokens that don't fit the pattern
    fall back to the raw integer so we don't silently lose data.
    """
    m = _DIR_TOKEN.search(token)
    if not m:
        return token
    raw = int(m.group(1))
    if raw % 10 == 0:
        return str(raw // 10)
    return str(raw)


def _parse_section_html(html: str, generation_date: date) -> SourceSection | None:
    """Turn one section-page HTML into a :class:`SourceSection`, or ``None``.

    Returns ``None`` when the page is not a readable section —
    404/redirect dummies that lack ``<div class="section-doc">``, or
    pages where the catchline is missing. Repealed-section placeholders
    (empty body after the catchline) also return ``None`` so the base
    runner counts them as skips.
    """
    doc_m = _SECTION_DOC.search(html)
    if not doc_m:
        return None
    doc_html = doc_m.group("body")

    cat_m = _CATCHLINE.search(doc_html)
    if not cat_m:
        return None
    citation_num = clean_text(cat_m.group("cite"))
    heading = clean_text(cat_m.group("heading")).rstrip(".").strip()

    # Body = everything inside section-doc with the catchline span
    # excised. Splitting on <p> preserves paragraph breaks — important
    # for subsection markers like ``(1)`` / ``(a)`` which live on their
    # own lines.
    body_html = doc_html[: cat_m.start()] + doc_html[cat_m.end() :]
    paras = [clean_text(b) for b in _PARA.findall(body_html)]
    paras = [p for p in paras if p]
    body = "\n\n".join(paras).strip()
    # MCA often leads the body with an em-dash following the heading
    # span. Trim leading dashes / whitespace so the source text is clean.
    body = body.strip("\u2014\u2013-").strip()

    if not body or body.lower() in {"repealed.", "repealed"}:
        return None

    work_number = citation_num
    citation = f"MCA \u00a7 {citation_num}"
    return SourceSection(
        jurisdiction="us-mt",
        doc_type="statute",
        authority_code="MCA",
        work_number=work_number,
        citation=citation,
        heading=heading,
        body=body,
        author_id="mt-legislature",
        author_name="Montana Legislature",
        author_url="https://leg.mt.gov",
        generation_date=generation_date,
    )


# --- TOC walkers ---------------------------------------------------------


def _list_titles() -> list[str]:
    """Return directory tokens (``title_0010`` etc.) for every linked title."""
    html = _fetch_text(f"{BASE}/index.html")
    return sorted(
        set(re.findall(r'href="\.?/?(title_\d+)/chapters_index\.html"', html))
    )


def _list_chapters(title_dir: str) -> list[str]:
    html = _fetch_text(f"{BASE}/{title_dir}/chapters_index.html")
    return sorted(
        set(re.findall(r'href="\.?/?(chapter_\d+)/parts_index\.html"', html))
    )


def _list_parts(title_dir: str, chapter_dir: str) -> list[str]:
    html = _fetch_text(f"{BASE}/{title_dir}/{chapter_dir}/parts_index.html")
    return sorted(
        set(re.findall(r'href="\.?/?(part_\d+)/sections_index\.html"', html))
    )


def _list_section_urls(
    title_dir: str, chapter_dir: str, part_dir: str
) -> list[str]:
    html = _fetch_text(
        f"{BASE}/{title_dir}/{chapter_dir}/{part_dir}/sections_index.html"
    )
    out: list[str] = []
    for m in _SECTION_ROW.finditer(html):
        out.append(
            f"{BASE}/{title_dir}/{chapter_dir}/{part_dir}/"
            f"{m.group('dir')}/{m.group('file')}"
        )
    return out


def _fetch_text(url: str) -> str:
    res = http_get(url)
    return res.text() if res else ""
