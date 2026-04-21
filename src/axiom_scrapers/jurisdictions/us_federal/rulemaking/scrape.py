"""Federal Register rulemaking scraper.

Source — `federalregister.gov/api/v1`
-------------------------------------
The Federal Register publishes the full daily output of agency
rulemaking through a clean JSON API. Four document types flow through:

* **Rule** — final rule (took effect or scheduled to)
* **Proposed Rule** — notice of proposed rulemaking
* **Notice** — non-rule agency announcements
* **Presidential Document** — executive orders, proclamations, etc.

This scraper targets **final Rules** and **Proposed Rules** — both
are rulemaking in the APA sense and share the same text format. The
heading for proposed rules gets a ``[Proposed]`` prefix so downstream
consumers can distinguish at a glance without parsing the body.
Presidential Documents (EOs, proclamations) are structurally
different policy and get their own scraper.

Architecture
------------
Two endpoints:

* ``/documents?conditions[type][]=RULE&conditions[publication_date][gte]={from}``
  returns the index of rules with metadata (``document_number``,
  ``title``, ``citation``, ``agencies``, ``raw_text_url``). We use
  ``gte`` to bound the walk to a recent window.
* ``raw_text_url`` for each rule returns an HTML envelope around a
  ``<pre>`` block with the full body text — stripped and written
  verbatim to the Section body.

Downstream
----------
Output is AKN XML keyed on the Federal Register document number
(``2026-07681``). Citation uses the FR form (``91 FR 20899``) when
present; document number is the stable identifier for deduplication.

Runtime cost
------------
Each rule is two HTTP calls (index page + body). Index pagination is
100 per page by default; one week of rules is typically 3-5 pages.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from axiom_scrapers._common import Scraper, Section, http_get

BASE = "https://www.federalregister.gov/api/v1"

#: Federal Register document types this scraper ingests. Both Rules
#: and Proposed Rules are APA rulemaking. Notices are mixed (some
#: policy, mostly not) so they're left out.
_DOC_TYPES: tuple[str, ...] = ("RULE", "PRORULE")

#: Heading prefix added to Proposed Rules so Atlas viewers can
#: distinguish them from final Rules without parsing the body.
_PROPOSED_PREFIX = "[Proposed] "

#: Window (in days) of publications to fetch when no explicit bounds
#: are provided. 7 days gives ~100-400 rules; workable smoke size.
_DEFAULT_WINDOW_DAYS = 7

#: Fields pulled from the index endpoint. Keep small — each extra field
#: is a few KB per result times thousands of results.
_INDEX_FIELDS = (
    "document_number",
    "title",
    "type",
    "citation",
    "publication_date",
    "agencies",
    "cfr_references",
    "html_url",
    "raw_text_url",
    "abstract",
)

#: Max results per index page (the API's ceiling).
_PER_PAGE = 100


@dataclass(frozen=True)
class FRDocRef:
    """Handle for one Federal Register document.

    ``document_number`` is the stable identifier (``2026-07681``).
    Rest of the fields are pulled from the index so we don't need to
    re-fetch per-document metadata.
    """

    document_number: str
    title: str
    citation: str  # e.g. "91 FR 20899"; may be "" before pagination
    publication_date: str  # ISO date
    raw_text_url: str
    agency_names: tuple[str, ...]
    #: FR-declared document type (``"Rule"`` or ``"Proposed Rule"``).
    #: Drives the heading prefix; not used for routing yet.
    fr_type: str = ""


class FederalRegisterRulemakingScraper(Scraper[FRDocRef]):
    """Scrape final Federal Register Rules.

    ``SectionRef`` is a :class:`FRDocRef` bundling all the metadata
    already returned by the index page. :meth:`parse_section` fetches
    the plain-text body once per doc.
    """

    jurisdiction = "us-federal"
    doc_type = "rulemaking"
    # "rulemaking" is already the collective noun — no pluralization.
    doc_type_dir = "rulemaking"
    authority_code = "FR"
    author_id = "us-federal-register"
    author_name = "U.S. Federal Register"
    author_url = "https://www.federalregister.gov"
    workers = 6

    #: Window in days relative to `generation_date`. Override for backfills.
    window_days: int = _DEFAULT_WINDOW_DAYS

    def list_sections(self) -> Iterable[FRDocRef]:
        start = (self.generation_date - timedelta(days=self.window_days)).isoformat()
        end = self.generation_date.isoformat()
        yield from _paged_index(_DOC_TYPES, start, end)

    def parse_section(self, ref: FRDocRef) -> Section | None:
        if not ref.raw_text_url:
            return None
        res = http_get(ref.raw_text_url)
        if res is None:
            return None
        body = extract_body_text(res.text())
        if not body:
            return None
        # FR citation is preferred; document_number is the fallback so
        # older pre-citation records still get a usable <num>.
        cite = ref.citation or f"FR Doc. {ref.document_number}"
        heading = ref.title
        if ref.fr_type == "Proposed Rule" and not heading.startswith(_PROPOSED_PREFIX):
            heading = _PROPOSED_PREFIX + heading
        return Section(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=ref.document_number,
            citation=cite,
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: Section) -> Path:
        """``us-federal/rulemaking/{YYYY}/{document_number}.xml``.

        Date from the work_number prefix (document numbers start with
        the publication year, e.g. ``2026-07681`` → year 2026).
        """
        year = section.work_number.split("-", 1)[0]
        safe = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            year,
            f"{safe}.xml",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


_BODY_START_RE = re.compile(r"<pre[^>]*>", re.IGNORECASE)
_BODY_END_RE = re.compile(r"</pre\s*>", re.IGNORECASE)
_HTML_ENTITY_RE = re.compile(r"<[^>]+>")


def extract_body_text(html: str) -> str:
    """Pull the plain-text body out of an FR raw-text page.

    The raw-text endpoint wraps the full document in
    ``<html><body><pre>...</pre></body></html>``. We slice between
    the ``<pre>`` tags, strip any remaining inline anchor tags (the
    wrapper inserts a GPO attribution link), decode HTML entities,
    and return the contents verbatim. Trailing whitespace is
    stripped but internal paragraph breaks are preserved.
    """
    start = _BODY_START_RE.search(html)
    end = _BODY_END_RE.search(html, start.end() if start else 0)
    if not start or not end:
        return ""
    body = html[start.end() : end.start()]
    # Inline <a>...</a> tags inside the <pre> block (e.g. the GPO
    # attribution link) survive the slice — strip them.
    body = _HTML_ENTITY_RE.sub("", body)
    # Decode HTML entities that crept in via the anchor strip.
    import html as _stdhtml

    body = _stdhtml.unescape(body)
    return body.strip()


def parse_index_results(index_json: str) -> list[FRDocRef]:
    """Parse one index-endpoint response into refs.

    Silently drops entries missing the document_number or raw_text_url;
    those happen for Presidential Documents whose text is published
    differently.
    """
    data = json.loads(index_json)
    if not isinstance(data, dict):
        return []
    results = data.get("results")
    if not isinstance(results, list):
        return []
    out: list[FRDocRef] = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        doc_number = entry.get("document_number")
        raw_text_url = entry.get("raw_text_url")
        if not doc_number or not raw_text_url:
            continue
        title = entry.get("title") or ""
        citation = entry.get("citation") or ""
        pub_date = entry.get("publication_date") or ""
        fr_type = entry.get("type") or ""
        agencies = entry.get("agencies") or []
        agency_names: tuple[str, ...] = tuple(
            a.get("name", "") for a in agencies if isinstance(a, dict)
        )
        out.append(
            FRDocRef(
                document_number=str(doc_number),
                title=str(title),
                citation=str(citation),
                publication_date=str(pub_date),
                raw_text_url=str(raw_text_url),
                agency_names=agency_names,
                fr_type=str(fr_type),
            )
        )
    return out


def _index_url(doc_types: tuple[str, ...], start: str, end: str, page: int) -> str:
    """Build one page's index URL with type + date filter."""
    parts = [
        f"per_page={_PER_PAGE}",
        f"page={page}",
        "order=newest",
        f"conditions[publication_date][gte]={start}",
        f"conditions[publication_date][lte]={end}",
    ]
    for t in doc_types:
        parts.append(f"conditions[type][]={t}")
    for f in _INDEX_FIELDS:
        parts.append(f"fields[]={f}")
    return f"{BASE}/documents?" + "&".join(parts)


def _paged_index(
    doc_types: tuple[str, ...], start: str, end: str
) -> Iterable[FRDocRef]:
    """Walk index pages until the API reports no next page."""
    page = 1
    while True:
        res = http_get(_index_url(doc_types, start, end, page))
        if res is None:
            return
        refs = parse_index_results(res.text())
        if not refs:
            return
        yield from refs
        try:
            data = json.loads(res.text())
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict) or not data.get("next_page_url"):
            return
        page += 1
