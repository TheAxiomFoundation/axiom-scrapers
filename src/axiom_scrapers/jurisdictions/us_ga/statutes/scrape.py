"""Official Code of Georgia Annotated (OCGA) scraper.

Source — `law.resource.org/pub/us/code/ga/`
-------------------------------------------
Georgia's OCGA is the paradigmatic "state with a hostile publisher": the
statutory text is produced by the Code Revision Commission but the
official public-access portal (``advance.lexis.com`` / ``lexisnexis.com
/hottopics/gacode``) is behind a JavaScript + cookie fingerprint wall,
and the ``law.justia.com/codes/georgia`` mirror is behind a Cloudflare
managed challenge that returns 403 to every User-Agent we've tried
(both our axiom-scraper UA and a Chrome desktop UA). The Georgia
legislature site (``legis.ga.gov``) runs a client-rendered SPA that
never ships statutory text on the wire.

The practical public source is Carl Malamud's archive at
Public.Resource.Org, which mirrors the OCGA's source-of-truth
distribution (the same Word/ODT files the Commission sends to
LexisNexis) under ``law.resource.org/pub/us/code/ga/``. Release 73
(2019-08-21) is the newest in that archive. The archive is:

* Machine-accessible (plain HTTP, no challenge, public-domain ODT/RTF).
* Official (same text the Commission ships to its publisher — the
  statutory portion itself is uncopyrightable per `Georgia v.
  Public.Resource.Org, Inc.`, 140 S. Ct. 1498 (2020)).
* Bulk (one ZIP, ~106 MB, contains all 53 titles as per-title ODT/RTF).

Release cadence in that archive is slow — 71/72/73 in 2019, nothing
since — so the text is dated relative to current OCGA amendments. This
is called out in :data:`DEFAULT_RELEASE` so operators can swap in a
newer release URL if one lands.

Document shape
--------------
Each title ODT (``gov.ga.ocga.2019.08.21.r73.title.{NN}.odt``) is an
OpenDocument container; ``content.xml`` holds the body. Per-section
layout in that XML is::

    <text:p><text:span text:style-name="T1">{id}. {heading}<text:line-break/></text:span></text:p>
    <text:p>Statute text</text:p>
    <text:p>(a) Body paragraph…</text:p>
    <text:p>(b) Body paragraph…</text:p>
    <text:p>History</text:p>
    <text:p>(Code 1981, § {id}; Ga. L. …)</text:p>
    <text:p>Annotations</text:p>
    <text:p>JUDICIAL DECISIONS</text:p>
    …

Style names (``P3``, ``P5``, ``T1``…) differ between titles, so we
anchor on the label *text* ("Statute text", "History", "Annotations")
rather than style names. Section ids are ``{title}-{chapter}-{section}``,
occasionally with a decimal suffix (``48-7-29.25``), and the ``T1``
span is stable across titles. Annotations / case notes / research
references are editorial overlay (not the law) and are excluded from
the emitted body.
"""

from __future__ import annotations

import html as _html
import io
import os
import re
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

from axiom_scrapers._common import Scraper, Section
from axiom_scrapers._common.http import DEFAULT_UA

DEFAULT_RELEASE = "gov.ga.ocga.2019.08.21.release.73"
BASE_URL = f"https://law.resource.org/pub/us/code/ga/{DEFAULT_RELEASE}.zip"
TITLE_ODT_TEMPLATE = "gov.ga.ocga.2019.08.21.r73.title.{:02d}.odt"
#: Georgia OCGA titles 1..53 (as of release 73 — 54+ are unpublished).
DEFAULT_TITLES: tuple[int, ...] = tuple(range(1, 54))

# Section header: <text:span style-name="T1">{id}. {heading}<text:line-break/>...
# The id is {title}-{chapter}-{section} with optional decimal
# (e.g. "48-7-29.25") and the heading ends at the ``<text:line-break/>``.
_HEADER_RE = re.compile(
    r'<text:span[^>]*text:style-name="T1"[^>]*>'
    r"(?P<id>\d+-\d+-\d+(?:\.\d+)?)\.\s*(?P<heading>.*?)"
    r"<text:line-break/>\s*</text:span>",
    re.DOTALL,
)

# Label paragraphs that delimit sub-blocks inside a section.
_STATUTE_TEXT_LABEL = re.compile(r">\s*Statute text\s*<")
# Body terminators: the next *non-statute-text* label or the em-dash
# chapter divider. We match on whichever comes first after the "Statute
# text" label.
_BODY_END_RE = re.compile(
    r">\s*(?:History|Annotations|JUDICIAL DECISIONS|RESEARCH REFERENCES|"
    r"OPINIONS OF THE ATTORNEY GENERAL|ALR)\s*<"
    r"|\u2014\u2014\u2014\u2014\u2014\u2014"
)

# One ODT paragraph.
_TEXT_P_RE = re.compile(r"<text:p\b[^>]*>(?P<inner>.*?)</text:p>", re.DOTALL)
# <text:s text:c="N"/>  — N spaces (default 1).
_TEXT_S_RE = re.compile(r'<text:s(?:\s+text:c="(\d+)")?\s*/>')
# <text:line-break/>  — paragraph-internal soft break.
_TEXT_BR_RE = re.compile(r"<text:line-break\s*/>")
# <text:tab/>  — tab.
_TEXT_TAB_RE = re.compile(r"<text:tab\s*/>")


@dataclass(frozen=True)
class OCGASectionRef:
    """Handle for one OCGA section — title number + full dashed id."""

    title: int  # 1..53
    section_id: str  # e.g. "1-1-10", "48-7-29.25"


class OCGAStatutesScraper(Scraper[OCGASectionRef]):
    """Scrape the Official Code of Georgia Annotated (OCGA).

    ``SectionRef`` is :class:`OCGASectionRef`. The ZIP is downloaded
    once (or loaded from ``cache_path`` if set), each title ODT is
    parsed, and ``(section_id -> heading, body)`` is cached in the
    instance so :meth:`parse_section` is pure lookup.
    """

    jurisdiction = "us-ga"
    doc_type = "statute"
    authority_code = "O.C.G.A."
    author_id = "ga-code-revision-commission"
    author_name = "Georgia Code Revision Commission"
    author_url = "https://law.resource.org/pub/us/code/ga/"
    workers = 1  # All I/O is local after the one-shot ZIP download.

    _titles: tuple[int, ...] = DEFAULT_TITLES

    def __init__(
        self,
        *,
        generation_date: date | None = None,
        cache_path: Path | str | None = None,
        zip_url: str = BASE_URL,
    ) -> None:
        super().__init__(generation_date=generation_date)
        self._zip_url = zip_url
        env_cache = os.environ.get("AXIOM_GA_OCGA_ZIP")
        if cache_path is not None:
            resolved_cache = Path(cache_path)
        elif env_cache:
            resolved_cache = Path(env_cache)
        else:
            resolved_cache = _default_cache_path()
        self._cache_path: Path = resolved_cache
        # section_id -> (title, heading, body)
        self._cache: dict[str, tuple[int, str, str]] = {}

    # --- public API ------------------------------------------------------

    def list_sections(self) -> Iterable[OCGASectionRef]:
        self._populate_cache()
        for sec_id, (title, _heading, _body) in self._cache.items():
            yield OCGASectionRef(title=title, section_id=sec_id)

    def parse_section(self, ref: OCGASectionRef) -> Section | None:
        entry = self._cache.get(ref.section_id)
        if entry is None:
            return None
        _title, heading, body = entry
        if not body:
            return None
        return Section(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=ref.section_id,
            citation=f"O.C.G.A. \u00a7 {ref.section_id}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: Section) -> Path:
        """``us-ga/statutes/title-{T}/title-{T}-sec-{id}.xml``.

        Section ids are ``{title}-{chapter}-{section}``, so splitting on
        the first ``-`` recovers the title for the directory prefix.
        """
        title = section.work_number.split("-", 1)[0]
        safe_section = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"title-{title}",
            f"title-{title}-sec-{safe_section}.xml",
        )

    # --- internals -------------------------------------------------------

    def _populate_cache(self) -> None:
        if self._cache:
            return
        zip_bytes = self._load_zip_bytes()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for title in self._titles:
                name = TITLE_ODT_TEMPLATE.format(title)
                try:
                    odt_bytes = zf.read(name)
                except KeyError:
                    continue
                content_xml = extract_content_xml(odt_bytes)
                for sec_id, heading, body in extract_sections(content_xml):
                    self._cache[sec_id] = (title, heading, body)

    def _load_zip_bytes(self) -> bytes:
        cache = self._cache_path
        if cache.exists() and cache.stat().st_size > 0:
            return cache.read_bytes()
        cache.parent.mkdir(parents=True, exist_ok=True)
        req = Request(self._zip_url, headers={"User-Agent": DEFAULT_UA})
        with urlopen(req, timeout=120) as resp:  # noqa: S310 (pinned URL)
            data: bytes = resp.read()
        cache.write_bytes(data)
        return data


# --- Pure-function helpers (tested in isolation) ----------------------


def extract_content_xml(odt_bytes: bytes) -> str:
    """Open an ODT (zip) and return its ``content.xml`` as text.

    Returns an empty string if the archive has no ``content.xml``.
    """
    with zipfile.ZipFile(io.BytesIO(odt_bytes)) as zf:
        try:
            return zf.read("content.xml").decode("utf-8", errors="replace")
        except KeyError:
            return ""


def extract_sections(content_xml: str) -> Iterator[tuple[str, str, str]]:
    """Yield ``(section_id, heading, body)`` tuples from a title content.xml.

    Body paragraphs are the text of every ``<text:p>`` between the
    "Statute text" label and the first of (History | Annotations |
    JUDICIAL DECISIONS | RESEARCH REFERENCES | OPINIONS OF THE ATTORNEY
    GENERAL | ALR | next section header | em-dash chapter divider).
    Editorial annotations are dropped.

    Callers get an iterator so large titles can stream without
    materializing every section at once.
    """
    headers = list(_HEADER_RE.finditer(content_xml))
    if not headers:
        return
    for i, m in enumerate(headers):
        sec_id = m.group("id")
        heading = _clean_inline(m.group("heading")).rstrip(".").strip()
        block_start = m.end()
        block_end = headers[i + 1].start() if i + 1 < len(headers) else len(content_xml)
        block = content_xml[block_start:block_end]

        st_m = _STATUTE_TEXT_LABEL.search(block)
        if st_m is None:
            # Rare generated sections skip the Statute text label;
            # skip rather than emit garbage.
            continue
        after_label = block[st_m.end() :]
        end_m = _BODY_END_RE.search(after_label)
        body_xml = after_label[: end_m.start()] if end_m else after_label
        body = _paragraphs_to_text(body_xml)
        yield sec_id, heading, body


def _paragraphs_to_text(xml_fragment: str) -> str:
    """Join every ``<text:p>`` inside ``xml_fragment`` with blank lines.

    Empty paragraphs are dropped; paragraph order is preserved.
    """
    paras: list[str] = []
    for m in _TEXT_P_RE.finditer(xml_fragment):
        p = _clean_inline(m.group("inner"))
        if p:
            paras.append(p)
    return "\n\n".join(paras)


def _clean_inline(xml_fragment: str) -> str:
    """Turn an ODT inline fragment into a plain string.

    * ``<text:s text:c="N"/>`` becomes N spaces (default 1).
    * ``<text:line-break/>`` becomes a single space — paragraph-internal
      soft breaks in OCGA aren't meaningful semantic breaks.
    * ``<text:tab/>`` becomes a single space.
    * All other ODT tags are stripped.
    * ``&amp;`` / ``&lt;`` / ``&gt;`` / ``&quot;`` / ``&apos;`` / numeric
      entities are decoded.
    * Runs of whitespace collapse to a single space.
    """
    s = _TEXT_S_RE.sub(lambda m: " " * (int(m.group(1)) if m.group(1) else 1), xml_fragment)
    s = _TEXT_BR_RE.sub(" ", s)
    s = _TEXT_TAB_RE.sub(" ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _default_cache_path() -> Path:
    """Return the default on-disk location for the cached OCGA ZIP."""
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "axiom-scrapers" / "us-ga" / f"{DEFAULT_RELEASE}.zip"
