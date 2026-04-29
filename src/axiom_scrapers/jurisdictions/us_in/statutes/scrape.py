"""Indiana Code (IC) scraper.

Source — `iga.in.gov`
---------------------
``iga.in.gov`` serves the Indiana Code as per-title HTML bundles
(usually 1-5 MB each) at::

    https://iga.in.gov/ic/{year}/Title_{N}.html

A companion JSON menu lives at ``Title_{N}.json`` — we use it only
for existence-probing (IN has gaps in its title numbering; a non-
existent title returns the same SPA shell).

Within each bundle, sections look like::

    <div class="section" id="2-2.2-2-1" ...>
      <span id="ic_number">IC 2-2.2-2-1</span>
      <span id="shortdescription">Deadline for filing...</span>
    </div>
    <p>Sec. 1. (a) ...body paragraphs...</p>
    <p><i>As added by P.L.123-2015, SEC.2.</i></p>

The trailing ``<i>As added by …</i>`` / ``<i>Amended by …</i>``
paragraph is publication history and is dropped (mirrors NV's
``SourceLine`` handling).

Parse strategy
--------------
:meth:`list_sections` fetches each title, splits the bundle into
sections via :func:`split_sections`, and caches
``(section_num, heading, body)`` on ``self._cache`` keyed by
``(title, div_id)``. :meth:`parse_section` is a cache read.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from axiom_scrapers._common import Scraper, SourceSection, clean_text, http_get

BASE = "https://iga.in.gov/ic"
DEFAULT_YEAR = "2024"

# Titles 1..36; several are unused. The SPA shell (~691 bytes) is served
# for missing titles; we use a small floor to distinguish shells from
# tiny-but-valid titles.
_MAX_TITLE = 36
_SHELL_SIZE_THRESHOLD = 2048

_SECTION_OPEN_RE = re.compile(
    r'<div\s+class="section"\s+id="(?P<id>[^"]+)"',
    re.IGNORECASE,
)
_ANY_STRUCTURE_OPEN = re.compile(
    r'<div\s+class="(?:section|chapter|article|title)"\s+id="[^"]+"',
    re.IGNORECASE,
)
_IC_NUMBER = re.compile(
    r'<span\s+id="ic_number"[^>]*>\s*IC\s+(?P<num>[0-9A-Za-z.\-]+)\s*</span>',
    re.IGNORECASE,
)
_SHORT_DESCRIPTION = re.compile(
    r'<span\s+id="shortdescription"[^>]*>(?P<desc>.*?)</span>',
    re.DOTALL | re.IGNORECASE,
)
_HISTORY_PARA = re.compile(
    r"<p[^>]*>\s*<span[^>]*>?\s*<i>\s*"
    r"(?:As added by|Amended by|Formerly:|As amended by)"
    r"[^<]*?</i>\s*</span>?\s*</p>",
    re.DOTALL | re.IGNORECASE,
)
_HISTORY_PARA_SIMPLE = re.compile(
    r"<p[^>]*>\s*<i>\s*"
    r"(?:As added by|Amended by|Formerly:|As amended by)"
    r"[^<]*?</i>\s*</p>",
    re.DOTALL | re.IGNORECASE,
)


class ICStatutesScraper(Scraper[tuple[str, str]]):
    """Scrape the Indiana Code.

    ``SectionRef`` is ``(title, div_id)``. ``div_id`` preserves
    version-variant suffixes (e.g. ``2-2.2-2-1-b`` for a successor
    version) so both versions survive to disk with unique filenames.
    """

    jurisdiction = "us-in"
    doc_type = "statute"
    authority_code = "IC"
    author_id = "in-legislature"
    author_name = "Indiana General Assembly"
    author_url = "https://iga.in.gov"
    workers = 4  # title bundles are ~1-5 MB each

    year: str = DEFAULT_YEAR

    def __init__(self, *, generation_date: date | None = None) -> None:
        super().__init__(generation_date=generation_date)
        # (title, div_id) -> (section_num, heading, body)
        self._cache: dict[tuple[str, str], tuple[str, str, str]] = {}

    def list_sections(self) -> Iterable[tuple[str, str]]:
        for title in _discover_titles(self.year):
            res = http_get(f"{BASE}/{self.year}/Title_{title}.html")
            if res is None:
                continue
            html = res.text()
            if len(html) < _SHELL_SIZE_THRESHOLD:
                continue
            for div_id, section_num, heading, body in split_sections(html, title):
                ref = (title, div_id)
                self._cache[ref] = (section_num, heading, body)
                yield ref

    def parse_section(self, ref: tuple[str, str]) -> SourceSection | None:
        cached = self._cache.get(ref)
        if cached is None:
            return None
        section_num, heading, body = cached
        if not body:
            return None
        title, div_id = ref
        # filename stem: portion of div_id after the title number
        if div_id.startswith(f"{title}-"):
            filename_stem = div_id[len(title) + 1 :]
        else:
            filename_stem = div_id
        work_number = f"{title}-{filename_stem}".replace("/", "_")
        return SourceSection(
            jurisdiction=self.jurisdiction,
            doc_type=self.doc_type,
            authority_code=self.authority_code,
            work_number=work_number,
            citation=f"IC {section_num}",
            heading=heading,
            body=body,
            author_id=self.author_id,
            author_name=self.author_name,
            author_url=self.author_url,
            generation_date=self.generation_date,
        )

    def relative_output_path(self, section: SourceSection) -> Path:
        """``us-in/statute/ch-{title}/ch-{title}-sec-{rest}.txt``."""
        title, rest = section.work_number.split("-", 1)
        return Path(
            self.jurisdiction,
            self._doc_type_dir(),
            f"ch-{title}",
            f"ch-{title}-sec-{rest}.txt",
        )


# --- Pure-function helpers (tested in isolation) -------------------------


def split_sections(html: str, title_num: str) -> list[tuple[str, str, str, str]]:
    """Return ``(div_id, section_num, heading, body)`` tuples.

    * ``div_id`` is the ``<div class="section" id="…">`` value — may
      include a version suffix like ``-b``.
    * ``section_num`` is the IC citation from the ``<span id="ic_number">``.
    * ``heading`` is the ``<span id="shortdescription">`` contents.
    * ``body`` is paragraph-joined text with publication-history
      paragraphs stripped.
    """
    out: list[tuple[str, str, str, str]] = []
    opens = list(_SECTION_OPEN_RE.finditer(html))
    for i, open_m in enumerate(opens):
        sec_id = open_m.group("id")
        if not (sec_id == title_num or sec_id.startswith(f"{title_num}-")):
            continue
        start = open_m.start()
        end: int | None = opens[i + 1].start() if i + 1 < len(opens) else None
        next_struct = _ANY_STRUCTURE_OPEN.search(html, pos=open_m.end())
        if next_struct and (end is None or next_struct.start() < end):
            end = next_struct.start()
        slab = html[start:end] if end is not None else html[start:]

        num_m = _IC_NUMBER.search(slab)
        section_num = num_m.group("num") if num_m else sec_id

        head_m = _SHORT_DESCRIPTION.search(slab)
        heading = clean_text(head_m.group("desc")) if head_m else ""
        heading = heading.rstrip(".").strip()

        header_end_m = re.search(
            r"</div>\s*(?:<p[^>]*>\s*</p>\s*)*", slab, re.IGNORECASE
        )
        body_html = slab[header_end_m.end() :] if header_end_m else slab
        body_html = _HISTORY_PARA.sub("", body_html)
        body_html = _HISTORY_PARA_SIMPLE.sub("", body_html)

        raw_paras = re.split(r"</p>|</li>", body_html, flags=re.IGNORECASE)
        paras = [clean_text(raw) for raw in raw_paras if clean_text(raw)]
        body = "\n\n".join(paras).strip().strip("—–-").strip()

        out.append((sec_id, section_num, heading, body))
    return out


def _discover_titles(year: str) -> list[str]:
    """Probe Title_{N}.json for N=1..36; return [title_num] for real titles."""
    out: list[str] = []
    for n in range(1, _MAX_TITLE + 1):
        res = http_get(f"{BASE}/{year}/Title_{n}.json")
        if res is None or len(res.body) < _SHELL_SIZE_THRESHOLD:
            continue
        try:
            entries = json.loads(res.text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(entries, list) or not entries:
            continue
        first = entries[0]
        if not isinstance(first, dict) or first.get("type") != "title":
            continue
        out.append(str(n))
    return out
