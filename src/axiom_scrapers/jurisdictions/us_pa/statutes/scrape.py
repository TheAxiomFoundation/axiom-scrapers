"""Pennsylvania Consolidated Statutes (Pa.C.S.) scraper.

Source — ``legis.state.pa.us``
-------------------------------
The Pennsylvania General Assembly publishes each title of the
Pennsylvania Consolidated Statutes as a single HTML document at::

    https://www.legis.state.pa.us/WU01/LI/LI/CT/HTM/{NN}/{NN}.HTM

For consolidated titles the body text is inlined into that one page
(Title 18 — Crimes and Offenses — is ~4.6 MB of HTML with ~2.4 MB of
visible text). Each section appears **twice** in the document — once in
the TOC at top, once as the body further down. Unconsolidated titles
(the ones whose primary enactment still lives in Purdon's Statutes
rather than the Consolidated Statutes) only publish a TOC and no body
text; we skip those by thresholding the visible character count.

Section markers render as ``&#167; {N}. &nbsp;Heading.`` followed by
body paragraphs until the next ``&#167;`` marker.

Output
------
Normalized source text plus sidecar metadata at
``{out}/us-pa/statutes/tt-{title:02d}/tt-{title:02d}-sec-{section}.txt``.

SectionRef
----------
Unlike most scrapers where :class:`~axiom_scrapers._common.base.Scraper`
parameterizes on a URL string, PA has many sections per title and the
title HTML is expensive to re-fetch (1-4 MB). :meth:`list_sections`
fetches each title's HTML *once*, splits it into per-section tuples,
and hands each :class:`PASectionRef` (title, section id, heading,
pre-extracted body) to :meth:`parse_section`. The parse step then only
builds the :class:`~axiom_scrapers._common.source_section.SourceSection` — no HTTP.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from axiom_scrapers._common import Scraper, SourceSection, clean_text, http_get

BASE = "https://www.legis.state.pa.us"

#: Pennsylvania numbers its Consolidated Statutes titles 1 through 82.
#: A handful of unconsolidated titles within that range publish TOC-only
#: pages (body text is still in Purdon's Statutes, not online as HTML);
#: they're filtered out by :data:`MIN_VISIBLE_CHARS`.
PA_TITLES: list[int] = list(range(1, 83))

#: A title's visible-text count below this threshold means the page is
#: a TOC-only shell (no inlined body). Empirically Title 18 has ~2.4 MB
#: of visible text; TOC-only titles come in around 150-200 KB.
MIN_VISIBLE_CHARS = 300_000

# "&#167; 901." — section marker. Section ids can include letters, dots,
# dashes (e.g. "5523.1", "1301-A").
_SECTION_SPLIT = re.compile(r"&#167;\s*(?P<num>\d+[\w.\-]*)\s*\.")

# Source-history trailer often appended at the end of a body
# ("(Source note…)" style — PA uses multiple shapes; strip the bracketed
# reference that starts a trailing parenthetical attribution line).
_SOURCE_TRAILER = re.compile(
    r"\(\s*(?:Source|Dec\.\s*\d|Added|Amended|Reenacted)[^)]*\)\s*$",
    re.IGNORECASE,
)

#: Minimum body length to emit a SourceSection. Below this, the chunk is
#: almost certainly a TOC entry that slipped past dedup (subchapter
#: header, cross-reference, etc.).
MIN_BODY_CHARS = 30


@dataclass(frozen=True)
class PASectionRef:
    """One section identified in a title's TOC-plus-body HTML.

    The raw body text (:attr:`body_text`) is already cleaned during
    :meth:`PAStatutesScraper.list_sections` so :meth:`parse_section`
    is a pure build step and can run in parallel against the
    :class:`~concurrent.futures.ThreadPoolExecutor` without repeating
    the 1-4 MB fetch.
    """

    title: int
    section: str
    heading: str
    body_text: str


class PAStatutesScraper(Scraper[PASectionRef]):
    """One scraper instance = one full Pa.C.S. scrape.

    The SectionRef is a :class:`PASectionRef` (not a URL string) because
    PA inlines many sections into a single title-level HTML document.
    Fetching per-section would re-download the same 1-4 MB per section;
    fetching per-title once during :meth:`list_sections` keeps the run
    roughly 1,000x more efficient on a title like 18 (with ~800
    sections).
    """

    jurisdiction = "us-pa"
    doc_type = "statute"
    authority_code = "PaCS"
    author_id = "pa-legislature"
    author_name = "Pennsylvania General Assembly"
    author_url = "https://www.legis.state.pa.us"
    workers = 8

    def list_sections(self) -> Iterable[PASectionRef]:
        """Fetch every title, yield one :class:`PASectionRef` per section."""
        for title in PA_TITLES:
            yield from _list_refs_for_title(title)

    def parse_section(self, ref: PASectionRef) -> SourceSection | None:
        """Build a :class:`SourceSection` from a pre-extracted ref. No HTTP."""
        if not ref.body_text or len(ref.body_text) < MIN_BODY_CHARS:
            return None
        work_number = f"{ref.title:02d}-{_safe_section(ref.section)}"
        citation = f"{ref.title} Pa.C.S. \u00a7 {ref.section}"
        return SourceSection(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=work_number,
            citation=citation,
            heading=ref.heading,
            body=ref.body_text,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: SourceSection) -> Path:
        """Nest by title so the tree stays browseable.

        Layout: ``us-pa/statute/tt-{title:02d}/tt-{title:02d}-sec-{section}.txt``.
        """
        title_token, safe_section = section.work_number.split("-", 1)
        filename = f"tt-{title_token}-sec-{safe_section}.txt"
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"tt-{title_token}",
            filename,
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def _list_refs_for_title(title: int) -> list[PASectionRef]:
    """Fetch one title's HTML and return refs for each of its sections.

    Unconsolidated titles (TOC-only; visible text below
    :data:`MIN_VISIBLE_CHARS`) return an empty list.
    """
    url = f"{BASE}/WU01/LI/LI/CT/HTM/{title:02d}/{title:02d}.HTM"
    res = http_get(url)
    if res is None:
        return []
    return parse_title_html(res.text(), title)


def parse_title_html(html: str, title: int) -> list[PASectionRef]:
    """Return per-section refs extracted from one title's HTML.

    Returns ``[]`` if the page is TOC-only (visible text below
    :data:`MIN_VISIBLE_CHARS`). Each section in the page appears twice
    (TOC entry + body) — this function keeps the **last** occurrence,
    which is always the body because body entries appear further down
    the document than their TOC counterparts.
    """
    if not _is_consolidated(html):
        return []
    return _split_sections(html, title)


def _is_consolidated(html: str) -> bool:
    """True when the page has substantive body text, not just a TOC."""
    visible_len = len(re.sub(r"<[^>]+>", " ", html))
    return visible_len >= MIN_VISIBLE_CHARS


def _split_sections(html: str, title: int) -> list[PASectionRef]:
    """Split HTML into ``PASectionRef`` entries, deduped by last occurrence.

    Dedup strategy
    --------------
    PA titles include each section twice — once in the TOC near the top,
    once as the body block lower down. We keep the **last** occurrence
    of each section number so we parse the body text, not the TOC stub.
    (TOC stubs have no body content; keeping the first occurrence would
    produce empty sections.)
    """
    all_matches = list(_SECTION_SPLIT.finditer(html))
    # Keep only the last occurrence of each section number.
    last_idx: dict[str, int] = {}
    for i, m in enumerate(all_matches):
        last_idx[m.group("num")] = i
    matches = [all_matches[i] for i in sorted(last_idx.values())]

    results: list[PASectionRef] = []
    for i, m in enumerate(matches):
        section = m.group("num")
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        raw = html[body_start:body_end]
        heading, body_text = _extract_heading_and_body(raw)
        results.append(
            PASectionRef(
                title=title,
                section=section,
                heading=heading,
                body_text=body_text,
            )
        )
    return results


def _extract_heading_and_body(raw_slab: str) -> tuple[str, str]:
    """Pull the heading sentence and the body text out of one section slab.

    PA's inline layout goes ``&#167; N.&nbsp;&nbsp;Heading.`` then body
    paragraphs separated by block tags. We clean the HTML, then take the
    first "sentence" (up to ``.`` + whitespace) as the heading and the
    remainder as the body. Trailing source-history parentheticals are
    stripped from the body.
    """
    cleaned = clean_text(raw_slab).lstrip()
    if not cleaned:
        return "", ""

    period = re.match(r"(?P<heading>[^.\n]+?)\.\s+(?P<body>.*)", cleaned, re.DOTALL)
    if period:
        heading = period.group("heading").strip().rstrip(".").strip()
        body = period.group("body").strip()
    else:
        # No body — only a heading-ish fragment (e.g. "Repealed." style).
        heading = cleaned.rstrip(".").strip()
        body = ""

    body = _SOURCE_TRAILER.sub("", body).strip()
    return heading, body


def _safe_section(section: str) -> str:
    """Turn a section id into a filename-safe path segment.

    ``/`` would break the nested output layout; everything else PA uses
    (dots, dashes, letters, digits) is fine on all common filesystems.
    """
    return section.replace("/", "_").strip()
