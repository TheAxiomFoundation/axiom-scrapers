"""BaseScraper — abstract base class for every per-state (or federal) scraper.

Design intent
-------------
Each concrete scraper supplies only the per-source logic:

* **What sections exist.** ``list_sections()`` yields identifiers
  (opaque to the base class) the scraper can later hand back to
  ``parse_section()``.
* **How to parse a section.** ``parse_section(ref)`` turns one
  identifier into a :class:`axiom_scrapers._common.akn.Section`.

The base handles everything else: parallel fetching (the caller's
logic is synchronous; the base provides a thread pool), error
surfacing, progress reporting, output path layout.

Writing a new scraper = subclass this + 30-80 lines of state-specific
regex.
"""

from __future__ import annotations

import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Generic, TypeVar

from .akn import Section, build_akn_xml
from .text import safe_path_segment

SectionRef = TypeVar("SectionRef")
LogFn = Callable[[str], None]


@dataclass(frozen=True)
class ScrapeResult:
    """Outcome of a run. Surfaces to CLI / test harness."""

    written: int
    skipped: int
    elapsed_seconds: float

    @property
    def total(self) -> int:
        return self.written + self.skipped


class Scraper(ABC, Generic[SectionRef]):
    """Abstract base class for a single-source scraper.

    Subclasses typically configure the class-level constants
    (``jurisdiction``, ``doc_type``, ``authority_code``, ``author_id``,
    ``author_name``, ``author_url``) and implement two methods.

    Subclassing pattern::

        class ILCSScraper(Scraper[str]):  # SectionRef = URL string
            jurisdiction = "us-il"
            doc_type = "statute"
            authority_code = "ILCS"
            author_id = "il-legislature"
            author_name = "Illinois General Assembly"
            author_url = "https://www.ilga.gov"

            def list_sections(self) -> Iterable[str]:
                for chapter in self._list_chapters():
                    for act in self._list_acts(chapter):
                        yield from self._list_section_urls(chapter, act)

            def parse_section(self, url: str) -> Section | None:
                ...
    """

    #: Full jurisdiction slug stored in AKN ``<FRBRcountry>`` and Axiom's
    #: ``jurisdiction`` column, e.g. ``"us-il"``, ``"us-federal"``, ``"uk"``.
    jurisdiction: str = ""

    #: Axiom doc_type value — ``"statute"``, ``"regulation"``,
    #: ``"guidance"``, ``"manual"``.
    doc_type: str = ""

    #: Short abbreviation of the cite format, e.g. ``"ILCS"``,
    #: ``"RCW"``. Written to ``<FRBRname>``.
    authority_code: str = ""

    #: Short id for the source author, e.g. ``"il-legislature"``.
    author_id: str = ""
    author_name: str = ""
    author_url: str = ""

    #: Parallel workers for :meth:`run`. States that rate-limit hard
    #: override this to a smaller number.
    workers: int = 6

    def __init__(self, *, generation_date: date | None = None) -> None:
        self.generation_date = generation_date or date.today()
        self._validate_config()

    def _validate_config(self) -> None:
        required = ("jurisdiction", "doc_type", "authority_code", "author_id", "author_name", "author_url")
        missing = [f for f in required if not getattr(self, f)]
        if missing:
            raise TypeError(
                f"{type(self).__name__} must set class attributes: {', '.join(missing)}"
            )

    # --- Subclass hooks ---------------------------------------------------

    @abstractmethod
    def list_sections(self) -> Iterable[SectionRef]:
        """Yield opaque identifiers for every section in the jurisdiction.

        The type parameter ``SectionRef`` lets subclasses use whatever
        shape fits — URLs, ``(chapter, section)`` tuples, filesystem
        paths. The base class treats them as opaque and hands each back
        to :meth:`parse_section`.
        """
        ...

    @abstractmethod
    def parse_section(self, ref: SectionRef) -> Section | None:
        """Return a parsed :class:`Section`, or ``None`` to skip.

        Returning ``None`` is the "soft fail" path — the section was
        repealed, had no body, or a live fetch failed. The run
        continues.
        """
        ...

    # --- Runner -----------------------------------------------------------

    def run(
        self,
        out_root: Path,
        *,
        limit: int | None = None,
        log_every: int = 100,
        logger: LogFn | None = None,
    ) -> ScrapeResult:
        """Scrape every section and write AKN XML under ``out_root``.

        Output layout
        -------------
        ``{out_root}/{jurisdiction}/{doc_type}/{section_id}.xml``.
        Each scraper can override :meth:`relative_output_path` if it
        wants a subdirectory per chapter / title.
        """
        log = logger or _default_logger
        started = time.time()
        written = 0
        skipped = 0
        submitted = 0

        # Consume ``list_sections`` lazily so parse workers start processing
        # as soon as the first ref is discovered — otherwise large corpora
        # like VA (tens of thousands of TOC pages) block the entire parse
        # phase behind a single-threaded discovery walk. Keeps the
        # submitted-but-unfinished queue bounded so memory stays flat even
        # for million-section jurisdictions.
        refs_iter = iter(self.list_sections())

        def _refill(ex: ThreadPoolExecutor, pending: dict[Any, None]) -> int:
            """Pull refs from the iterator, submit to the pool.

            Returns the number submitted in this call.
            """
            slots = max(0, self.workers * 4 - len(pending))
            added = 0
            for _ in range(slots):
                try:
                    ref = next(refs_iter)
                except StopIteration:
                    return added
                if limit is not None and submitted + added >= limit:
                    return added
                fut = ex.submit(self._parse_and_write, ref, out_root)
                pending[fut] = None
                added += 1
            return added

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            pending: dict[Any, None] = {}
            submitted += _refill(ex, pending)
            log(f"Scraping {self.jurisdiction}/{self.doc_type} (streaming discovery)")
            exhausted = not pending
            while pending:
                # Drain one future at a time so refills happen promptly.
                done_iter = as_completed(pending)
                fut = next(done_iter)
                del pending[fut]
                ok = fut.result()
                if ok:
                    written += 1
                else:
                    skipped += 1
                seen = written + skipped
                if log_every > 0 and seen % log_every == 0:
                    elapsed = time.time() - started
                    log(
                        f"  {seen}: {written} ok, {skipped} skipped, "
                        f"{elapsed/60:.1f} min"
                    )
                if not exhausted:
                    added = _refill(ex, pending)
                    submitted += added
                    if added == 0 and (limit is None or submitted < limit):
                        exhausted = True

        elapsed = time.time() - started
        log(
            f"DONE {self.jurisdiction}/{self.doc_type} — "
            f"{written} ok, {skipped} skipped, {elapsed/60:.1f} min"
        )
        return ScrapeResult(
            written=written, skipped=skipped, elapsed_seconds=elapsed
        )

    def _parse_and_write(self, ref: SectionRef, out_root: Path) -> bool:
        try:
            sec = self.parse_section(ref)
        except Exception as exc:  # Soft-fail: log, count as skip, continue.
            print(
                f"  WARN parse failed for {ref!r}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            return False
        if sec is None:
            return False
        dest = out_root / self.relative_output_path(sec)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(build_akn_xml(sec), encoding="utf-8")
        return True

    #: Plural directory segment for this doc_type in the output tree.
    #: Axiom's ingester walks ``{repo}/statutes/`` / ``{repo}/regulations/``
    #: etc. — plural. Override if the pluralization isn't just ``+s``
    #: (``rulemaking`` is already plural; overrides set ``"rulemaking"``).
    doc_type_dir: str = ""

    def relative_output_path(self, section: Section) -> Path:
        """Return the output filename for a section, relative to ``out_root``.

        Default layout::

            {jurisdiction}/{doc_type_dir}/{section_id}.xml

        where ``doc_type_dir`` is the plural directory segment
        (``statutes``, ``regulations``) that matches what Axiom's
        ingester walks.

        Subclasses often override to nest by chapter / title so the
        tree stays browseable:

            {jurisdiction}/{doc_type_dir}/ch-{chapter}/{section_id}.xml
        """
        safe = safe_path_segment(section.work_number)
        return Path(self.jurisdiction) / self._doc_type_dir() / f"{safe}.xml"

    def _doc_type_dir(self) -> str:
        """Plural directory segment; defaults to ``{doc_type}s``."""
        return self.doc_type_dir or f"{self.doc_type}s"


def _default_logger(msg: str) -> None:
    print(msg, flush=True)
