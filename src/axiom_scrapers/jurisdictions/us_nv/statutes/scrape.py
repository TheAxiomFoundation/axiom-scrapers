"""Nevada Revised Statutes (NRS) scraper.

Source — `leg.state.nv.us/NRS/`
-----------------------------
The Nevada Legislature publishes the entire NRS as one HTML page per
chapter::

    https://www.leg.state.nv.us/NRS/NRS-{chapter}.html

Each page is a Word-exported HTML bundle encoded as Windows-1252. A
section starts with an anchor and is laid out like this::

    <p class="SectBody">
      <span class="Empty">      <a name=NRS244Sec010></a>NRS </span>
      <span class="Section">244.010</span>
      <span class="Empty">  </span>
      <span class="Leadline">Minimum number of county commissioners.</span>
      <span class="Empty">  </span>
      {body paragraphs…}
    </p>
    <p class="SourceNote">      [1:70:1883; …](Added to NRS by 1971, …)</p>

The chapter token can contain letters (``244``, ``244A``, ``360B``);
the section number is always ``{chapter}.{nnn}`` where ``nnn`` may
include letters, decimals, or dashes. :func:`_parse_sections` parses
the full page into a list of :class:`Section` objects, splitting on the
``<a name=NRS...Sec...>`` anchor boundaries.
"""

from __future__ import annotations

import re
import sys
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from axiom_scrapers._common import (
    Scraper,
    ScrapeResult,
    Section,
    build_akn_xml,
    clean_text,
    http_get,
)

BASE = "https://www.leg.state.nv.us/NRS"

# NV pages are Windows-1252 encoded (Microsoft Word output). We pass
# ``cp1252`` to :meth:`FetchResult.text` rather than relying on the
# response's declared charset — some chapter pages omit the HTTP
# Content-Type charset.
_NV_ENCODING = "cp1252"

# Each section is bracketed by an anchor tag. The ``name`` attribute is
# unquoted (Word HTML), so the regex doesn't require quote chars.
# Chapter token allows digits + uppercase letters (``244``, ``244A``);
# section token allows digits + letters (``010``, ``0105``, ``123A``).
_SECTION_ANCHOR = re.compile(
    r"<a\s+name=NRS(?P<chapter>[0-9A-Z]+)Sec(?P<section>[0-9A-Za-z]+)>\s*</a>"
)

# Canonical section number, e.g. "244.010", in its own span right after
# the anchor.
_SECTION_SPAN = re.compile(
    r'<span\s+class="Section"[^>]*>\s*([^<]+?)\s*</span>',
    re.DOTALL,
)

# Section heading — the first ``<span class="Leadline">`` or
# ``<span class="COLeadline">`` after the section span. Both classes
# exist in the NV corpus; ``COLeadline`` appears on chapter-opening
# (cross-office) sections.
_LEADLINE_SPAN = re.compile(
    r'<span\s+class="(?:Leadline|COLeadline)"[^>]*>(.*?)</span>',
    re.DOTALL,
)

# Source-history paragraphs — NRS uses ``SourceNote`` (not
# ``SourceLine`` as some Word exports name it). These are citation
# history, not body text, and must be stripped before emitting AKN.
# The regex accepts both class names so we don't silently regress if
# NV renames the class.
_SOURCE_PARA = re.compile(
    r'<p\s+class="(?:SourceNote|SourceLine)"[^>]*>.*?</p>',
    re.DOTALL,
)


class NRSStatutesScraper(Scraper[tuple[str, str]]):
    """One scraper instance = one full NRS scrape.

    The section reference is ``(chapter_token, filename)`` — a tuple
    rather than a URL because each NV chapter page contains many
    sections. NRS serves whole chapters per HTTP call, so per-section
    fanout would fire ~300× more requests than needed. We override
    :meth:`run` to fetch chapters in parallel and expand each page
    into N sections in-memory before writing. The :class:`Scraper`
    contract still fits: :meth:`parse_section` is retained for
    interface compliance (returns the first section of the chapter)
    but the production path is through :meth:`run`.
    """

    jurisdiction = "us-nv"
    doc_type = "statute"
    authority_code = "NRS"
    author_id = "nv-legislature"
    author_name = "Nevada Legislature"
    author_url = "https://www.leg.state.nv.us"
    workers = 6

    def list_sections(self) -> Iterable[tuple[str, str]]:
        """Yield ``(chapter_token, filename)`` for every NRS chapter page.

        The NRS landing page at ``{BASE}/`` lists chapter files as
        ``<a href="NRS-{chapter}.html">``. We parse that index and emit
        one entry per chapter — each will be expanded into many
        sections by :meth:`parse_section`.
        """
        res = http_get(f"{BASE}/")
        if res is None:
            return
        html = res.text(_NV_ENCODING)
        for filename in _list_chapter_files(html):
            chapter = _chapter_token(filename)
            yield (chapter, filename)

    def parse_section(self, ref: tuple[str, str]) -> Section | None:
        """Fetch one chapter page and return its *first* parsed section.

        .. warning::
           The :class:`Scraper` base class assumes one ``ref`` →
           zero-or-one :class:`Section`, but NRS chapters contain
           hundreds of sections per page. See :meth:`run` override.
        """
        # Single-section callers rarely hit this — the overridden
        # :meth:`run` expands each chapter fetch into many Section
        # objects directly. Kept for interface compliance.
        _chapter, filename = ref
        res = http_get(f"{BASE}/{filename}")
        if res is None:
            return None
        sections = _parse_sections(
            res.text(_NV_ENCODING),
            generation_date=self.generation_date,
        )
        return sections[0] if sections else None

    def run(
        self,
        out_root: Path,
        *,
        limit: int | None = None,
        log_every: int = 100,
        logger: Callable[[str], None] | None = None,
    ) -> ScrapeResult:
        """Chapter-page fanout: each fetch yields many sections.

        NRS serves whole chapters per HTTP call, so the per-section
        fanout pattern used by IL would fire ~300× more requests than
        needed. We override :meth:`run` to fetch chapter pages in
        parallel, then expand each page into N sections in-memory and
        write them out.
        """
        log = logger or _default_logger
        started = time.time()
        written = 0
        skipped = 0

        refs = list(self.list_sections())
        if limit is not None:
            refs = refs[:limit]
        total = len(refs)
        log(f"Scraping {total} chapters for {self.jurisdiction}/{self.doc_type}")

        def _fetch_chapter(ref: tuple[str, str]) -> list[Section]:
            _chapter, filename = ref
            try:
                res = http_get(f"{BASE}/{filename}")
                if res is None:
                    return []
                return _parse_sections(
                    res.text(_NV_ENCODING),
                    generation_date=self.generation_date,
                )
            except Exception as exc:  # soft-fail, mirrors base class behavior
                print(f"  WARN parse failed for {filename!r}: {exc}", file=sys.stderr, flush=True)
                return []

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futures = {ex.submit(_fetch_chapter, ref): ref for ref in refs}
            for fut in as_completed(futures):
                sections = fut.result()
                for sec in sections:
                    dest = out_root / self.relative_output_path(sec)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(build_akn_xml(sec), encoding="utf-8")
                    written += 1
                if not sections:
                    skipped += 1
                seen = written + skipped
                if log_every > 0 and seen % log_every == 0:
                    elapsed = time.time() - started
                    log(f"  {seen}: {written} ok, {skipped} skipped, {elapsed / 60:.1f} min")

        elapsed = time.time() - started
        log(
            f"DONE {self.jurisdiction}/{self.doc_type} — "
            f"{written} ok, {skipped} skipped, {elapsed / 60:.1f} min"
        )
        return ScrapeResult(written=written, skipped=skipped, elapsed_seconds=elapsed)

    def relative_output_path(self, section: Section) -> Path:
        """Nest by chapter so the tree stays browseable::

        {jurisdiction}/{doc_type}/ch-{chapter}/{section}.xml
        """
        # work_number is ``{chapter}.{section}`` — first dot-separated
        # token is the chapter token (may include letters: ``244A``).
        chapter = section.work_number.split(".", 1)[0]
        safe_section = section.work_number.replace("/", "_")
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"ch-{chapter}",
            f"{safe_section}.xml",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def _parse_sections(html: str, generation_date: date) -> list[Section]:
    """Parse a full NRS chapter HTML page into :class:`Section` objects.

    Parsing strategy
    ----------------
    1. Split the page on ``<a name=NRS{chapter}Sec{id}>`` anchors.
    2. For each anchor-bounded slab:
       * Pull the canonical section number from the first
         ``<span class="Section">`` (preserves decimal form ``244.010``).
       * Pull the heading from the first ``<span class="Leadline">`` or
         ``<span class="COLeadline">``; strip trailing period.
       * Remove the Section + Leadline spans and any
         ``<p class="SourceNote">`` paragraphs (source-history citations,
         not body text).
       * Clean what remains into the body.
    3. Empty-body sections (rare in practice — chapter 244 has none —
       but expected in chapters containing repealed sections) are
       dropped.

    The CRLF line-endings in NV's Word export are normalized to LF
    before :func:`clean_text` runs, so paragraph splitting (which keys
    on ``\\n\\n``) works without stray ``\\r`` chars polluting the output.
    """
    # Pre-normalize CRLF → LF. NV's Word HTML ships with CRLF and
    # ``clean_text``'s whitespace regex only collapses ``[ \t]``, so
    # without this step carriage returns would leak into the AKN body.
    html = html.replace("\r\n", "\n").replace("\r", "\n")

    anchors = list(_SECTION_ANCHOR.finditer(html))
    out: list[Section] = []
    for i, m in enumerate(anchors):
        # ``m.group("chapter")`` / ``m.group("section")`` are the
        # anchor-derived tokens. The canonical section number from
        # ``<span class="Section">`` inside the slab is authoritative,
        # so we only use the match for its position.
        start = m.end()
        end = anchors[i + 1].start() if i + 1 < len(anchors) else len(html)
        slab = html[start:end]

        sec_m = _SECTION_SPAN.search(slab)
        if sec_m is None:
            # No canonical section span — skip. This guards against
            # stray anchors that occasionally appear outside SectBody
            # paragraphs (e.g. table-of-contents backlinks).
            continue
        section_num = clean_text(sec_m.group(1))
        if not section_num:
            continue

        head_m = _LEADLINE_SPAN.search(slab)
        heading = clean_text(head_m.group(1)).rstrip(".").strip() if head_m else ""

        # Strip the Section + Leadline spans and any SourceNote paragraphs.
        body_html = slab
        if sec_m:
            body_html = body_html[sec_m.end() :]
        body_html = _LEADLINE_SPAN.sub("", body_html, count=1)
        body_html = _SOURCE_PARA.sub("", body_html)

        body = clean_text(body_html)
        # Strip stray em/en-dashes Word left around section-number spans.
        body = body.strip().strip("\u2014\u2013-").strip()

        if not body or body.lower() in {"repealed.", "repealed"}:
            continue

        out.append(
            Section(
                jurisdiction="us-nv",
                doc_type="statute",
                authority_code="NRS",
                work_number=section_num,
                citation=f"NRS {section_num}",
                heading=heading,
                body=body,
                author_id="nv-legislature",
                author_name="Nevada Legislature",
                author_url="https://www.leg.state.nv.us",
                generation_date=generation_date,
            )
        )

    return out


def _list_chapter_files(index_html: str) -> list[str]:
    """Return sorted unique ``NRS-*.html`` filenames from the NRS index."""
    return sorted(set(re.findall(r'href="(NRS-[^"]+\.html)"', index_html)))


def _chapter_token(filename: str) -> str:
    """Extract the chapter token from ``NRS-244.html`` → ``244``."""
    m = re.match(r"NRS-(.+)\.html$", filename)
    return m.group(1) if m else filename


def _default_logger(msg: str) -> None:
    """Default run-progress logger (module-level for picklability)."""
    print(msg, flush=True)
