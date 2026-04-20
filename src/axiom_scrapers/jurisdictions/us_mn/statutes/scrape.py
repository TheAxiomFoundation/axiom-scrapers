"""Minnesota Statutes scraper.

Source — `revisor.mn.gov`
------------------------
The site has three discovery layers:

1. Root ``/statutes/`` lists topical *parts*, linking to
   ``/statutes/part/<NAME>``.
2. Each part page lists chapters as ``/statutes/cite/<chapter>``;
   chapters are numeric with optional letter suffix (``1``, ``2A``,
   ``13D``).
3. Chapter pages ``/statutes/cite/<chapter>`` link sections as
   ``/statutes/cite/<chapter>.<nn>``.

Each section page wraps the body in::

    <div class="section" id="stat.1.01">
      <h1 class="shn">1.01 EXTENT.</h1>
      <p>The sovereignty and jurisdiction of this state…</p>
      <div class="subd">...subdivision blocks if any...</div>
    </div>
    <div class="history">...</div>

Repealed sections appear as ``<div class="sr">`` and carry no body.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from axiom_scrapers._common import Scraper, Section, clean_text, http_get

BASE = "https://www.revisor.mn.gov"

_PART_LINK_RE = re.compile(
    r'href="(https?://www\.revisor\.mn\.gov/statutes/part/[^"]+)"',
    re.IGNORECASE,
)

_CHAPTER_LINK_RE = re.compile(
    r'href="(?:https?://www\.revisor\.mn\.gov)?/statutes/cite/([0-9]+[A-Z]?)"',
    re.IGNORECASE,
)

_SECTION_BLOCK_RE = re.compile(
    r'<div\s+class="section"\s+id="stat\.(?P<section>[0-9A-Za-z.]+)"\s*>'
    r"(?P<body>.*?)"
    r'</div>\s*(?=<div\s+class="(?:history|sr)"|</div>\s*(?:<!--|$))',
    re.DOTALL,
)

_REPEALED_BLOCK_RE = re.compile(
    r'<div\s+class="sr"\s+id="stat\.(?P<section>[0-9A-Za-z.]+)"\s*>.*?</div>',
    re.DOTALL,
)

_SUBD_BLOCK_RE = re.compile(
    r'<div\s+class="subd"[^>]*>(?P<body>.*?)</div>',
    re.DOTALL,
)


class MinnStatutesScraper(Scraper[str]):
    """Scrape the Minnesota Statutes.

    ``SectionRef`` is the section token (e.g. ``"1.01"``). URLs are
    constructed from the token; the base runner fetches each section
    page in parallel.
    """

    jurisdiction = "us-mn"
    doc_type = "statute"
    authority_code = "Minn. Stat."
    author_id = "mn-legislature"
    author_name = "Minnesota Legislature"
    author_url = "https://www.revisor.mn.gov"
    workers = 6

    def list_sections(self) -> Iterable[str]:
        for chapter in _list_all_chapters():
            yield from _list_chapter_sections(chapter)

    def parse_section(self, ref: str) -> Section | None:
        res = http_get(f"{BASE}/statutes/cite/{ref}")
        if res is None:
            return None
        parsed = parse_section_page(res.text(), ref)
        if parsed is None:
            return None
        heading, body = parsed
        if not body:
            return None
        return Section(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=ref,
            citation=f"Minn. Stat. \u00a7 {ref}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: Section) -> Path:
        """``us-mn/statute/ch-{chapter}/ch-{chapter}-sec-{section}.xml``."""
        chapter = section.work_number.split(".", 1)[0]
        safe_section = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self.doc_type,
            f"ch-{chapter}",
            f"ch-{chapter}-sec-{safe_section}.xml",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def parse_section_page(html: str, section: str) -> tuple[str, str] | None:
    """Return ``(heading, body)`` for ``section``, or ``None`` if repealed/missing.

    ``body`` is newline-joined paragraphs; subdivisions appear as their
    own paragraph groups preceded by the ``Subd. N. Headnote`` marker.
    """
    if _REPEALED_BLOCK_RE.search(html):
        return None

    m = _SECTION_BLOCK_RE.search(html)
    body_html: str
    if m:
        body_html = m.group("body")
    else:
        loose = re.search(
            rf'<div\s+class="section"\s+id="stat\.{re.escape(section)}"\s*>'
            r'(.*?)(?=<div\s+class="history"|</div>\s*</div>\s*<)',
            html,
            re.DOTALL,
        )
        if not loose:
            return None
        body_html = loose.group(1)

    shn_m = re.search(r'<h1\s+class="shn"[^>]*>(.*?)</h1>', body_html, re.DOTALL)
    heading = ""
    if shn_m:
        raw = _clean_mn(shn_m.group(1))
        raw = re.sub(rf"^{re.escape(section)}\s*", "", raw)
        heading = raw.rstrip(".").strip()
        body_html = body_html[: shn_m.start()] + body_html[shn_m.end() :]

    body_html = _SUBD_BLOCK_RE.sub(_subd_repl, body_html)
    body = _clean_mn(body_html)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return heading, body


def extract_chapter_tokens(html: str) -> list[str]:
    """Return chapter tokens referenced on a part/index page."""
    tokens: set[str] = set()
    for m in _CHAPTER_LINK_RE.finditer(html):
        tokens.add(m.group(1))

    def sort_key(tok: str) -> tuple[int, str]:
        key = re.match(r"(\d+)([A-Z]*)", tok)
        return (int(key.group(1)), key.group(2)) if key else (0, tok)

    return sorted(tokens, key=sort_key)


def extract_section_tokens(html: str, chapter: str) -> list[str]:
    """Return section tokens linked from a chapter page (preserving order)."""
    pat = rf'href="(?:https?://www\.revisor\.mn\.gov)?/statutes/cite/({re.escape(chapter)}\.[0-9A-Za-z]+)"'
    seen: set[str] = set()
    out: list[str] = []
    for match in re.findall(pat, html):
        if match not in seen:
            seen.add(match)
            out.append(match)
    return out


def extract_part_urls(html: str) -> list[str]:
    """Return part URLs referenced on the root statutes page."""
    return sorted(set(_PART_LINK_RE.findall(html)))


def _subd_repl(match: re.Match[str]) -> str:
    inner = match.group("body")
    hdr_m = re.search(r'<h2\s+class="subd_no"[^>]*>(.*?)</h2>', inner, re.DOTALL)
    hdr_text = _clean_mn(hdr_m.group(1)) if hdr_m else ""
    body_inner = inner[: hdr_m.start()] + inner[hdr_m.end() :] if hdr_m else inner
    body_inner_clean = _clean_mn(body_inner)
    if hdr_text and body_inner_clean:
        return f"\n\n{hdr_text}\n{body_inner_clean}"
    if body_inner_clean:
        return f"\n\n{body_inner_clean}"
    return ""


def _clean_mn(s: str) -> str:
    """MN-specific cleaning: strip pilcrow permalinks + headnote spacing."""
    s = re.sub(
        r'<a[^>]*class="permalink"[^>]*>.*?</a>',
        "",
        s,
        flags=re.DOTALL | re.IGNORECASE,
    )
    s = re.sub(
        r'<span\s+class="headnote"[^>]*>',
        " ",
        s,
        flags=re.IGNORECASE,
    )
    return clean_text(s)


def _list_all_chapters() -> list[str]:
    """Walk parts → chapters, returning every chapter token."""
    root = http_get(f"{BASE}/statutes/")
    if root is None:
        return []
    chapters: set[str] = set()
    for part_url in extract_part_urls(root.text()):
        part_res = http_get(part_url)
        if part_res is None:
            continue
        for ch in extract_chapter_tokens(part_res.text()):
            chapters.add(ch)

    def sort_key(tok: str) -> tuple[int, str]:
        key = re.match(r"(\d+)([A-Z]*)", tok)
        return (int(key.group(1)), key.group(2)) if key else (0, tok)

    return sorted(chapters, key=sort_key)


def _list_chapter_sections(chapter: str) -> list[str]:
    res = http_get(f"{BASE}/statutes/cite/{chapter}")
    if res is None:
        return []
    return extract_section_tokens(res.text(), chapter)
