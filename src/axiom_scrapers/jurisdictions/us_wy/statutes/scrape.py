"""Wyoming Statutes scraper.

Source — ``wyoleg.gov`` (Folio/Rocket NXT CMS)
----------------------------------------------
Wyoming publishes statutes through an older Folio/Rocket NXT site at
``wyoleg.gov/NXT/gateway.dll``. The modern Angular SPA at the bare
``wyoleg.gov`` domain renders entirely client-side and exposes no
statute API; the LSO Service JSON endpoint covers Bills/Calendar/etc.
but not statutes; and Justia / FindLaw are blocked behind Cloudflare
on most non-residential IPs. NXT is the only first-party path.

Despite the older chrome, NXT exposes two clean stateless XHR
endpoints that we can crawl with plain ``http_get`` (no cookies, no
search session):

* ``f=xmlcontents&command=getchildren&basepathid={id}`` — returns
  ``<toc><nodes><n .../></nodes></toc>`` describing the children of
  one TOC node. Nodes carry ``ct`` (``application/folder`` →
  recurse, ``text/xml`` → leaf document) and ``id`` (the slash-
  separated path used to fetch the document).
* ``/{encoded_doc_path}?f=templates&fn=document-frameset.htm`` —
  serves the statute HTML for a leaf node (chapter or article).

Site shape
~~~~~~~~~~
Three-deep navigation under each annual snapshot::

    2025 Wyoming Statutes / 2025 Titles / {Title} / {Chapter}
                                                    ├── (text/xml leaf — sections in chapter)
                                                    └── {Article}  (text/xml leaf — sections in article)

Each leaf document interleaves ``<div class="Section">`` (heading +
citation) with ``<div class="Normal-Level"><div class="Normal|L2|L3|L4|L5">``
body paragraphs. Sections within one document are siblings — we split
on the next ``<div class="Section">`` opener (or end-of-body) to attach
the right body to each heading.

Year pin
~~~~~~~~
:data:`YEAR` is set to the latest annual snapshot NXT publishes. NXT
keeps prior years online; bumping the constant is the only change
needed when a new edition is released.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from axiom_scrapers._common import (
    Scraper,
    SourceSection,
    clean_paragraphs,
    clean_text,
    http_get,
)

#: Latest annual statute snapshot NXT publishes. Bump when a new edition
#: lands (verifiable via the root TOC: ``f=xmlcontents&command=getchildren``).
YEAR = "2025"

#: NXT gateway. All traffic is plain GET — no cookies, no auth.
GATEWAY = "https://wyoleg.gov/NXT/gateway.dll"

#: ``vid`` selects the publication; Wyoming has only the one ``Publish``
#: collection. Required on every NXT request.
VID = "Publish:10.1048/Enu"

#: Root TOC node for the year's titles. The ``Constitution`` / ``Rules``
#: / ``Water`` siblings under the year are scraped under separate
#: doc_types, not as statutes.
TITLES_ROOT_ID = f"{YEAR} Wyoming Statutes/{YEAR} Titles"

#: Cap on TOC ``getchildren`` page size. Wyoming's deepest title (Title
#: 35 — Public Health) holds <100 chapters, comfortably within this.
TOC_MAX_NODES = 500


# --- TOC XML --------------------------------------------------------------

# `<n ct="..." hc="y" id="..." n="..." t="...">` — flat-attribute TOC node.
# Attributes are emitted in a stable order but we match on the named
# captures rather than position so a future reorder doesn't break us.
_TOC_NODE_RE = re.compile(
    r'<n\s+([^>]*?)/?>',
    re.DOTALL,
)
_TOC_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


@dataclass(frozen=True)
class TocNode:
    """One node from an ``f=xmlcontents`` response.

    Attributes mirror the XML attribute names. ``ct`` distinguishes
    ``application/folder`` (recurse via ``getchildren``) from
    ``text/xml`` (fetch the leaf document) and ``application/subdocument``
    (a section anchor inside its parent leaf).
    """

    id: str
    name: str  # the short name attribute ``n`` (e.g. "1", "3")
    title: str  # the display title ``t`` (e.g. "TITLE 1 - CODE OF CIVIL PROCEDURE")
    content_type: str  # ``ct``


def extract_toc_nodes(xml: str) -> list[TocNode]:
    """Return the immediate children listed in an xmlcontents response.

    NXT nests grandchildren inline when ``hc="y"`` and they were already
    populated, so we only return *immediate* children — the ones whose
    enclosing ``<n>`` tag appears at the top level of the ``<nodes>``
    container. Recursion into grandchildren is the caller's job (one
    ``getchildren`` request per level).
    """
    # Locate the <nodes>...</nodes> envelope.
    nodes_m = re.search(r"<nodes>(.*?)</nodes>", xml, re.DOTALL)
    if not nodes_m:
        return []
    body = nodes_m.group(1)

    # Walk top-level <n ...> openers, skipping any nested <n> inside.
    out: list[TocNode] = []
    depth = 0
    i = 0
    while i < len(body):
        if body.startswith("<n ", i) or body.startswith("<n\t", i):
            # Find the end of this opening tag (handles self-close `/>` too).
            end = body.find(">", i)
            if end == -1:
                break
            tag_inner = body[i + 2 : end].rstrip("/").strip()
            self_close = body[end - 1] == "/"
            if depth == 0:
                attrs = dict(_TOC_ATTR_RE.findall(tag_inner))
                node_id = attrs.get("id", "")
                if node_id:
                    out.append(
                        TocNode(
                            id=node_id,
                            name=attrs.get("n", ""),
                            title=attrs.get("t", ""),
                            content_type=attrs.get("ct", ""),
                        )
                    )
            if not self_close:
                depth += 1
            i = end + 1
        elif body.startswith("</n>", i):
            depth = max(0, depth - 1)
            i += 4
        else:
            i += 1
    return out


# --- Section-block parsing -------------------------------------------------

# Section heading line lives inside the first inner ``<div>`` of a
# ``<div class="Section">`` block:
#   <div class="Section"><span ...></span><div>1-2-101.&nbsp;&nbsp;Form.</div></div>
# Citation token (``1-2-101``) is the canonical work number; the rest
# is the heading text. Some headings carry a trailing period; some are
# blank (typically reserved/repealed slots).
_SECTION_HEADING_RE = re.compile(
    r'<div\s+class="Section">.*?<div>\s*'
    r'(?P<work>[0-9A-Za-z]+(?:[-.][0-9A-Za-z]+)+)\s*\.?\s*'
    r'(?P<heading>.*?)\s*</div>\s*</div>',
    re.DOTALL | re.IGNORECASE,
)

#: Opening tag for a section block — used to slice the document into
#: per-section chunks.
_SECTION_OPEN_RE = re.compile(r'<div\s+class="Section">', re.IGNORECASE)


@dataclass(frozen=True)
class WySectionRef:
    """Pre-parsed handle for one Wyoming statute section.

    The TOC walk in :meth:`WyoStatutesScraper.list_sections` fetches
    each chapter / article HTML once and slices it into per-section
    refs. Carrying the parsed body here avoids re-fetching the parent
    document inside :meth:`WyoStatutesScraper.parse_section`, which
    would otherwise multiply NXT load by ~30x (typical sections per
    article).
    """

    work_number: str  # canonical citation token, e.g. "1-2-101"
    title: str  # title number, e.g. "1"
    heading: str  # section heading text
    body: str  # paragraph-separated body
    source_url: str  # URL of the parent leaf document


def iter_section_blocks(html: str) -> Iterable[tuple[str, str]]:
    """Yield ``(heading_html, body_html)`` slices, one per section.

    NXT's leaf documents (chapter or article HTML) interleave headings
    and bodies as flat sibling ``<div>``s — the only structural marker
    is the ``<div class="Section">`` opener. We treat each opener as
    a section boundary: everything up to (but not including) the next
    opener is that section's body. The final section's body runs to
    the closing ``</body>`` (or end of input).
    """
    starts = [m.start() for m in _SECTION_OPEN_RE.finditer(html)]
    if not starts:
        return
    # Cap the last section at the *last* </body> (NXT pages embed an
    # earlier </body> literal inside an inlined ``win.document.write``
    # string in the header, which a naive first-match would hit). Fall
    # back to end-of-string if none.
    body_matches = list(re.finditer(r"</body>", html, re.IGNORECASE))
    end_cap = body_matches[-1].start() if body_matches else len(html)
    boundaries = starts + [end_cap]
    for i in range(len(starts)):
        start = boundaries[i]
        end = boundaries[i + 1]
        block = html[start:end]
        # Locate the heading-closing ``</div></div>`` that ends the
        # ``<div class="Section">…</div>`` opener block. After that
        # everything until the next boundary is body markup.
        head_end_m = re.search(
            r'<div\s+class="Section">.*?</div>\s*</div>',
            block,
            re.DOTALL | re.IGNORECASE,
        )
        if not head_end_m:
            # Malformed section — skip rather than mis-parse.
            continue
        heading_html = head_end_m.group(0)
        body_html = block[head_end_m.end():]
        yield heading_html, body_html


def extract_section_id_and_heading(heading_html: str) -> tuple[str, str] | None:
    """Return ``(work_number, heading_text)`` from a heading block.

    Returns ``None`` if the block doesn't match the canonical Wyoming
    citation shape (``D-D-DDD`` with optional letter / decimal suffix).
    """
    m = _SECTION_HEADING_RE.search(heading_html)
    if not m:
        return None
    work = m.group("work").strip()
    heading = clean_text(m.group("heading")).rstrip(".").strip()
    return work, heading


def parse_section_body(body_html: str) -> str:
    """Return paragraph-separated text for a section body.

    NXT uses ``<div class="Normal">`` for top-level paragraphs and
    ``<div class="L2|L3|L4|L5">`` for indented sub-paragraphs.
    Indented divs are paragraphs in their own right (lists like
    ``(a) Foo: (i) Bar; (ii) Baz.``), so we treat every block-level
    div the same — :func:`clean_paragraphs` collapses runs of
    blank-paragraph spacers into a single boundary.
    """
    return clean_paragraphs(body_html).strip()


# --- Year-extraction helpers ---------------------------------------------


def extract_title_from_work_number(work: str) -> str:
    """Return the title token from a Wyoming citation.

    Wyoming citations are dashed triples ``T-C-S`` where ``T`` is the
    title (no decimals — Wyoming hasn't subdivided any titles), ``C``
    is the chapter, and ``S`` is the section. Some sections carry a
    decimal suffix on the section component (``1-39-104.5``).
    """
    return work.split("-", 1)[0]


# --- Scraper ---------------------------------------------------------------


class WyoStatutesScraper(Scraper[WySectionRef]):
    """Crawl the Wyoming Statutes via NXT's xmlcontents + document-frameset endpoints.

    Discovery (``list_sections``) walks the TOC year → titles → titles →
    chapters → articles, fetching each leaf document once and yielding
    one :class:`WySectionRef` per ``<div class="Section">`` block found
    inside it. ``parse_section`` then has zero network work — it just
    builds the :class:`SourceSection` from the ref.
    """

    jurisdiction = "us-wy"
    doc_type = "statute"
    authority_code = "Wyo. Stat. Ann."
    author_id = "wy-legislature"
    author_name = "Wyoming Legislature"
    author_url = "https://wyoleg.gov"
    # NXT tolerates a few parallel doc fetches but the xmlcontents
    # endpoint serializes internally; keeping workers modest avoids
    # 500-storms on the discovery walk.
    workers = 4

    def list_sections(self) -> Iterable[WySectionRef]:
        for title_node in self._fetch_children(TITLES_ROOT_ID):
            if title_node.content_type != "application/folder":
                continue
            for leaf in self._iter_leaf_documents(title_node):
                yield from self._sections_from_leaf(title_node, leaf)

    def parse_section(self, ref: WySectionRef) -> SourceSection | None:
        if not ref.body:
            return None
        return SourceSection(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=ref.work_number,
            citation=f"Wyo. Stat. Ann. § {ref.work_number}",
            heading=ref.heading,
            body=ref.body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: SourceSection) -> Path:
        """``us-wy/statutes/title-{T}/{section_id}.txt``."""
        title = extract_title_from_work_number(section.work_number)
        safe = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"title-{title}",
            f"{safe}.txt",
        )

    # --- Internal helpers --------------------------------------------------

    def _iter_leaf_documents(self, title_node: TocNode) -> Iterable[TocNode]:
        """Walk a title's subtree and yield every ``text/xml`` node.

        Wyoming titles are two-or-three deep: title → chapter (``text/xml``
        leaf) OR title → chapter (``application/folder``) → article
        (``text/xml`` leaf). Either way the leaf is the document
        carrying ``<div class="Section">`` blocks for individual
        statutes.
        """
        for chapter in self._fetch_children(title_node.id):
            if chapter.content_type == "text/xml":
                yield chapter
            elif chapter.content_type == "application/folder":
                for article in self._fetch_children(chapter.id):
                    if article.content_type == "text/xml":
                        yield article

    def _sections_from_leaf(
        self, title_node: TocNode, leaf: TocNode
    ) -> Iterable[WySectionRef]:
        url = build_document_url(leaf.id)
        res = http_get(url)
        if res is None:
            return
        html = res.text()
        for heading_html, body_html in iter_section_blocks(html):
            parsed = extract_section_id_and_heading(heading_html)
            if parsed is None:
                continue
            work, heading = parsed
            body = parse_section_body(body_html)
            if not body:
                continue
            yield WySectionRef(
                work_number=work,
                title=title_node.name,
                heading=heading,
                body=body,
                source_url=url,
            )

    def _fetch_children(self, basepathid: str) -> list[TocNode]:
        url = build_xmlcontents_url(basepathid)
        res = http_get(url)
        if res is None:
            return []
        return extract_toc_nodes(res.text())


# --- URL builders (pure, tested) ----------------------------------------


def build_xmlcontents_url(basepathid: str) -> str:
    """Build a TOC ``getchildren`` URL for a given basepath id.

    NXT URL-encodes the ``basepathid`` value (``/`` and spaces) into
    the query string. We use ``quote`` rather than ``quote_plus`` so
    the resulting URL is what NXT's own JS produces — slashes in the
    id are preserved as ``%2F``, spaces as ``%20``.
    """
    qs = {
        "f": "xmlcontents",
        "command": "getchildren",
        "basepathid": basepathid,
        "maxnodes": str(TOC_MAX_NODES),
        "vid": VID,
    }
    return f"{GATEWAY}?{urllib.parse.urlencode(qs, quote_via=urllib.parse.quote)}"


def build_document_url(node_id: str) -> str:
    """Build the document-frameset URL for a ``text/xml`` leaf node.

    Doc-path traversal: NXT carries the document path as a literal
    ``/{encoded}`` slug after ``gateway.dll``. The path is
    component-encoded — slashes preserved, spaces ``%20``.
    """
    encoded = urllib.parse.quote(node_id, safe="/")
    qs = urllib.parse.urlencode(
        {"f": "templates", "fn": "document-frameset.htm", "vid": VID},
        quote_via=urllib.parse.quote,
    )
    return f"{GATEWAY}/{encoded}?{qs}"
