"""Arkansas Code scraper.

Source — `law.resource.org/pub/us/code/ar/arkansas.xml.2012`
-----------------------------------------------------------
The current Arkansas Code is published only through LexisNexis (the
redirect chain from ``arkleg.state.ar.us/ArkansasLaw`` lands on
``advance.lexis.com``'s JavaScript SPA). Justia and FindLaw mirror
the Code but both sit behind Cloudflare JS challenges that refuse
non-interactive requests — verified with a realistic Chrome
User-Agent.

Public.Resource.Org hosts a complete XML snapshot of the 2012
Arkansas Code — 28 per-title files covering ~35,000 sections — as
part of its state-code archive. It is the only machine-parseable,
non-JavaScript source for the full Arkansas Code. The archive is
stale (pre-2013 amendments only); downstream callers should refresh
a section against Lexis before relying on it for current-law
questions.

XML shape
---------
Each title file wraps all sections in a nested ``<Index>`` tree::

    <Content Type="Statutes">
      <Indexes>
        <Index Level="1" HasChildren="1">        <!-- Title -->
          <Caption>Title 1</Caption>
          <Description>...</Description>
          <Indexes>
            <Index Level="N" HasChildren="0">    <!-- Section leaf -->
              <Caption>Section 1-1-101</Caption>
              <Description>Heading text</Description>
              <Content>HTML-encoded body</Content>
              <ShortName>Ark. Code 1-1-101</ShortName>
              <RevisionHistory>HISTORY: ...</RevisionHistory>
            </Index>
            ...
          </Indexes>
        </Index>
      </Indexes>
    </Content>

Sections may be nested under Title → Chapter, Title → Chapter →
Subchapter, or Title → Chapter → Subchapter → Part — the leaf level
varies across titles. The parser walks the tree recursively and
identifies sections by a ``Section {id}`` caption. Range/list
captions such as ``Section 1-1-101 -- 1-1-105`` mark reserved or
repealed blocks and are skipped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

from axiom_scrapers._common import Scraper, Section, clean_paragraphs, http_get

# Per-title bulk XML — {tt} is zero-padded (01..28).
BASE = "https://law.resource.org/pub/us/code/ar/arkansas.xml.2012"

#: Title numbers the archive publishes. The Arkansas Code has 28
#: titles; ``01`` uses zero-padding because that's the filename
#: convention used by Public.Resource.Org.
_TITLES: tuple[str, ...] = tuple(f"{n:02d}" for n in range(1, 29))

# A single-section caption is "Section {id}" where id starts with a
# digit and may contain digits, letters, dots and dashes (e.g.
# ``4-2A-101``, ``17-100-101``). Range and list captions contain
# spaces ("-- " or ", ") and therefore don't match.
_SECTION_ID_RE = re.compile(r"^Section\s+([0-9][0-9A-Za-z\.\-]*)$")


@dataclass(frozen=True)
class ARSectionRef:
    """Handle for one Arkansas Code section.

    Attributes
    ----------
    title
        Zero-padded title token as published by the source filename,
        e.g. ``"01"``, ``"26"``.
    section
        Full dashed section id, e.g. ``"1-1-101"``, ``"4-2A-101"``.
    """

    title: str
    section: str


class ARCodeStatutesScraper(Scraper[ARSectionRef]):
    """Scrape the 2012 Arkansas Code snapshot from Public.Resource.Org.

    Per-title bulk fetch. Each of the 28 XML files is fetched once in
    :meth:`list_sections`, parsed into all of its sections, and the
    resulting ``(heading, body)`` cached for thread-safe lookup in
    :meth:`parse_section`.
    """

    jurisdiction = "us-ar"
    doc_type = "statute"
    authority_code = "Ark. Code Ann."
    author_id = "ar-legislature"
    author_name = "Arkansas General Assembly"
    author_url = "https://www.arkleg.state.ar.us"
    # Only 28 files; a small pool keeps the fetch polite and avoids
    # hammering Public.Resource.Org's archive server.
    workers = 4

    _titles: tuple[str, ...] = _TITLES

    def __init__(self, *, generation_date: date | None = None) -> None:
        super().__init__(generation_date=generation_date)
        self._cache: dict[ARSectionRef, tuple[str, str]] = {}

    def list_sections(self) -> Iterable[ARSectionRef]:
        for tt in self._titles:
            res = http_get(f"{BASE}/ar.code.{tt}.2012.xml")
            if res is None:
                continue
            xml = res.text()
            if not xml:
                continue
            for section_num, heading, body in parse_title_xml(xml):
                if not body:
                    continue
                ref = ARSectionRef(title=tt, section=section_num)
                self._cache[ref] = (heading, body)
                yield ref

    def parse_section(self, ref: ARSectionRef) -> Section | None:
        cached = self._cache.get(ref)
        if cached is None:
            return None
        heading, body = cached
        return Section(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=ref.section,
            citation=f"Ark. Code Ann. \u00a7 {ref.section}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: Section) -> Path:
        """``us-ar/statutes/title-{T}/title-{T}-sec-{id}.xml``.

        The title prefix comes from the first dash-delimited segment
        of the section id (which is the title number in the Arkansas
        citation format, e.g. ``1-1-101`` → title ``1``).
        """
        title = section.work_number.split("-", 1)[0]
        safe_section = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"title-{title}",
            f"title-{title}-sec-{safe_section}.xml",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def parse_title_xml(xml: str) -> list[tuple[str, str, str]]:
    """Return ``(section_id, heading, body)`` for every section in a title XML.

    Walks the full ``<Index>`` tree regardless of nesting depth, so
    sections published at Level 3 (Title → Chapter → Section) and at
    Level 5 (Title → Chapter → Subchapter → Part → Section) are both
    captured. Captions that describe a range (``Section 1-1-101 -- 1-1-105``)
    or list (``Section 1-1-101, 1-1-102``) are reserved/repealed
    markers and are skipped.
    """
    root = ET.fromstring(xml)
    out: list[tuple[str, str, str]] = []
    for idx in root.iter("Index"):
        caption = (idx.findtext("Caption") or "").strip()
        m = _SECTION_ID_RE.match(caption)
        if not m:
            continue
        section_id = m.group(1)
        heading_raw = (idx.findtext("Description") or "").strip()
        content_raw = idx.findtext("Content") or ""
        if not content_raw.strip():
            continue
        # ElementTree already decoded the XML layer, so content_raw now
        # carries the source's inline HTML (<br>, <b>, <font>, &nbsp;,
        # &#167;). clean_paragraphs normalizes that into paragraph-split
        # plain text; adjacent <br><br> survives as a \n\n gap that
        # build_akn_xml turns into separate <p> elements.
        body = clean_paragraphs(content_raw)
        heading = heading_raw.rstrip(".").strip()
        out.append((section_id, heading, body))
    return out
