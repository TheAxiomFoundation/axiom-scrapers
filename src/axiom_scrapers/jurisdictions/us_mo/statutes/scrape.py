"""Missouri Revised Statutes (RSMo) scraper.

Source — `revisor.mo.gov`
-------------------------
The RSMo is ASP.NET pages at ``https://www.revisor.mo.gov``:

* ``Home.aspx`` — master navigation linking every chapter via
  ``/main/OneChapter.aspx?chapter={chapter}`` hrefs.
* ``OneChapter.aspx?chapter={chapter}`` — chapter index listing
  sections as ``/main/PageSelect.aspx?section={chapter}.{sec}&bid=…``.
* ``OneSection.aspx?section={chapter}.{sec}`` — the section body.
  Heading appears in ``<meta property="og:description">`` and the body
  text is in ``<p class="norm">`` paragraphs inside
  ``<div class="norm">``, with a trailing ``<div class="foot">``
  holding the enactment history (stripped).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from axiom_scrapers._common import Scraper, Section, clean_paragraphs, clean_text, http_get

BASE = "https://www.revisor.mo.gov"

_CHAPTER_LINK_RE = re.compile(
    r"/main/OneChapter\.aspx\?chapter=([0-9A-Za-z]+)"
)

_SECTION_LINK_RE = re.compile(
    r"PageSelect\.aspx\?section=([0-9A-Za-z.]+)(?:&amp;|&)bid=\d+"
)

_NORM_BLOCK_RE = re.compile(
    r'<div\s+class="norm"[^>]*>(?P<body>.*?)</div>\s*<hr\s*/?>',
    re.DOTALL | re.IGNORECASE,
)

_FOOT_BLOCK_RE = re.compile(
    r'<div\s+class="foot"[^>]*>.*?</div>', re.DOTALL | re.IGNORECASE
)

_LEAD_BOLD_RE = re.compile(
    r'<span\s+class="bold"[^>]*>'
    r"(?P<lead>(?:[^<]|<span[^>]*>[^<]*</span>|<(?!/?span))*?)"
    r"</span>",
    re.DOTALL | re.IGNORECASE,
)

_OG_DESC_RE = re.compile(
    r'<meta\s+property="og:description"\s+content="(?P<d>[^"]*)"',
    re.IGNORECASE,
)


class RSMoStatutesScraper(Scraper[str]):
    """Scrape Missouri Revised Statutes.

    ``SectionRef`` is the section token (e.g. ``"1.010"``). Chapter is
    the prefix before the first dot.
    """

    jurisdiction = "us-mo"
    doc_type = "statute"
    authority_code = "RSMo"
    author_id = "mo-legislature"
    author_name = "Missouri General Assembly"
    author_url = "https://www.revisor.mo.gov"
    workers = 6

    def list_sections(self) -> Iterable[str]:
        for chapter in _list_all_chapters():
            for section in _list_chapter_sections(chapter):
                # Defensive: only keep sections whose prefix matches the chapter.
                if section.split(".", 1)[0] != chapter:
                    continue
                yield section

    def parse_section(self, ref: str) -> Section | None:
        res = http_get(f"{BASE}/main/OneSection.aspx?section={ref}")
        if res is None:
            return None
        heading, body = parse_section_page(res.text(), ref)
        if not body:
            return None
        return Section(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=ref,
            citation=f"\u00a7 {ref}, RSMo",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: Section) -> Path:
        chapter = section.work_number.split(".", 1)[0]
        safe_section = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"ch-{chapter}",
            f"ch-{chapter}-sec-{safe_section}.xml",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def parse_section_page(html: str, section: str) -> tuple[str, str]:
    """Return ``(heading, body)``; empty strings if body not present.

    The leading ``<span class="bold">`` carries the section number +
    heading + em-dash separator. The heading also appears in
    ``<meta property="og:description">`` as a fallback.
    """
    m = _NORM_BLOCK_RE.search(html)
    if not m:
        return ("", "")
    body_html = m.group("body")
    body_html = _FOOT_BLOCK_RE.sub("", body_html)

    heading = ""
    lb = _LEAD_BOLD_RE.search(body_html)
    if lb:
        lead = clean_text(lb.group("lead"))
        lead = re.sub(rf"^\s*{re.escape(section)}\.\s*", "", lead)
        heading = lead.strip().rstrip("—–-").strip().rstrip(".")
        body_html = _LEAD_BOLD_RE.sub("", body_html, count=1)

    if not heading:
        md = _OG_DESC_RE.search(html)
        if md:
            heading = clean_text(md.group("d")).rstrip(".")

    # Body keeps paragraph breaks: each <p class="norm"> → <p> in AKN output.
    body = clean_paragraphs(body_html).lstrip("—–-").strip()
    return (heading, body)


def extract_chapter_tokens(html: str) -> list[str]:
    """Return chapter tokens from a Home.aspx page."""
    tokens = set(_CHAPTER_LINK_RE.findall(html))

    def sort_key(tok: str) -> tuple[int, int | str, str]:
        m = re.match(r"^(\d+)([A-Za-z]*)$", tok)
        if m:
            return (0, int(m.group(1)), m.group(2))
        return (1, tok, "")

    return sorted(tokens, key=sort_key)


def extract_section_tokens(html: str) -> list[str]:
    """Return section tokens linked from a OneChapter page, preserving order."""
    seen: dict[str, None] = {}
    for m in _SECTION_LINK_RE.finditer(html):
        sec = m.group(1)
        if sec not in seen:
            seen[sec] = None
    return list(seen)


def _list_all_chapters() -> list[str]:
    res = http_get(f"{BASE}/main/Home.aspx")
    if res is None:
        return []
    return extract_chapter_tokens(res.text())


def _list_chapter_sections(chapter: str) -> list[str]:
    res = http_get(f"{BASE}/main/OneChapter.aspx?chapter={chapter}")
    if res is None:
        return []
    return extract_section_tokens(res.text())
