"""Nebraska Revised Statutes scraper.

Source — `nebraskalegislature.gov`
----------------------------------
Nebraska publishes one section per page::

    https://nebraskalegislature.gov/laws/statutes.php?statute={chapter}-{section}

Chapter TOCs live at ``browse-chapters.php?chapter={N}`` and list
each section via::

    <a href="/laws/statutes.php?statute=1-101">
      <span class="sr-only">View Statute </span>1-101
    </a>

Each section page wraps its body in ``<div class="statute">`` with
``<h2>`` (section number), ``<h3>`` (heading), and ``<p class="text-justify">``
body paragraphs. A trailing ``<div>`` holds the ``<h2>Source</h2>``
cite history we drop.

Repealed sections render as heading ``"Repealed. Laws …"`` with no
body paragraphs — those are soft-skipped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import quote

from axiom_scrapers._common import Scraper, Section, clean_text, http_get

BASE = "https://nebraskalegislature.gov/laws"

# Nebraska has 90 numeric chapters.
_CHAPTERS = tuple(range(1, 91))

_SECTION_LINK_RE = re.compile(
    r'<a\s+href="/laws/statutes\.php\?statute=(?P<stat>[0-9A-Za-z.\-]+)"'
    r'>\s*<span class="sr-only">View Statute\s*</span>\s*(?P=stat)\s*</a>',
    re.IGNORECASE,
)

_STATUTE_DIV_RE = re.compile(
    r'<div\s+class="statute"[^>]*>(.*?)(?=<div\s+class="card-footer"|'
    r"</div>\s*</div>\s*<nav|</div>\s*</div>\s*</div>)",
    re.IGNORECASE | re.DOTALL,
)

_H2_NUM_RE = re.compile(
    r"<h2[^>]*>\s*(?P<num>[0-9A-Za-z.\-,]+?)\s*\.?\s*</h2>",
    re.IGNORECASE | re.DOTALL,
)

_H3_HEAD_RE = re.compile(
    r"<h3[^>]*>(?P<heading>.*?)</h3>", re.IGNORECASE | re.DOTALL
)

_BODY_PARA_RE = re.compile(
    r'<p\s+class="text-justify"[^>]*>(?P<para>.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)


class NebRevStatScraper(Scraper[tuple[int, str]]):
    """Scrape Nebraska Revised Statutes.

    ``SectionRef`` is ``(chapter, stat_token)`` where ``stat_token``
    is the full ``{chapter}-{section}`` id used by the source URL.
    """

    jurisdiction = "us-ne"
    doc_type = "statute"
    authority_code = "NebRevStat"
    author_id = "ne-legislature"
    author_name = "Nebraska Legislature"
    author_url = "https://nebraskalegislature.gov"
    workers = 6

    def list_sections(self) -> Iterable[tuple[int, str]]:
        for chapter in _CHAPTERS:
            for token in _list_chapter_section_tokens(chapter):
                yield (chapter, token)

    def parse_section(self, ref: tuple[int, str]) -> Section | None:
        _chapter, stat_token = ref
        res = http_get(f"{BASE}/statutes.php?statute={quote(stat_token)}")
        if res is None:
            return None
        parsed = parse_section_page(res.text(), stat_token)
        if parsed is None:
            return None
        section_num, heading, body = parsed
        if not body:
            return None
        return Section(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=section_num,
            citation=f"Neb. Rev. Stat. \u00a7 {section_num}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: Section) -> Path:
        """``us-ne/statute/ch-{chapter}/ch-{chapter}-sec-{rest}.xml``."""
        chapter = section.work_number.split("-", 1)[0]
        rest = (
            section.work_number[len(chapter) + 1 :]
            if section.work_number.startswith(f"{chapter}-")
            else section.work_number
        )
        safe_section = rest.replace("/", "_")
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"ch-{chapter}",
            f"ch-{chapter}-sec-{safe_section}.xml",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def parse_section_page(html: str, expected_token: str) -> tuple[str, str, str] | None:
    """Extract ``(section_num, heading, body)`` from a section page.

    Returns ``None`` when:
    * the page has no ``<div class="statute">`` wrapper (NE's 200-plus-
      plain-text error response for invalid statute ids), or
    * the heading begins with ``Repealed.`` and there are no body paras.
    """
    div_m = _STATUTE_DIV_RE.search(html)
    if not div_m:
        return None
    slab = div_m.group(1)

    slab_body = re.sub(
        r"<div[^>]*>\s*<h2[^>]*>\s*Source\s*</h2>.*?</div>",
        "",
        slab,
        flags=re.IGNORECASE | re.DOTALL,
    )

    num_m = _H2_NUM_RE.search(slab_body)
    section_num = (
        num_m.group("num").strip().rstrip(".") if num_m else expected_token
    )

    head_m = _H3_HEAD_RE.search(slab_body)
    heading = ""
    if head_m:
        heading = clean_text(head_m.group("heading")).rstrip(".")

    paras: list[str] = []
    for m in _BODY_PARA_RE.finditer(slab_body):
        para = clean_text(m.group("para"))
        if para:
            paras.append(para)

    body = "\n\n".join(paras)
    if not body and heading.lower().startswith("repealed"):
        return None
    return section_num, heading, body


def extract_section_tokens(html: str, chapter: int) -> list[str]:
    """Return ``{chapter}-…`` statute tokens listed on a chapter TOC."""
    prefix = f"{chapter}-"
    seen: set[str] = set()
    tokens: list[str] = []
    for m in _SECTION_LINK_RE.finditer(html):
        tok = m.group("stat")
        if not tok.startswith(prefix):
            continue
        if tok in seen:
            continue
        seen.add(tok)
        tokens.append(tok)
    return tokens


def _list_chapter_section_tokens(chapter: int) -> list[str]:
    res = http_get(f"{BASE}/browse-chapters.php?chapter={chapter}")
    if res is None:
        return []
    return extract_section_tokens(res.text(), chapter)
