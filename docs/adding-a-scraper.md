# Adding a scraper

A new scraper is four things: a module, a registry entry, a test, and a
fixture. Budget: 30-90 minutes depending on how gnarly the source
HTML is.

## 1. Probe the source

Before writing code, find:

1. **A root URL** that lists every chapter / title / section.
2. **A section URL pattern** — ideally deterministic (template + IDs).
3. **A sample section** — pick something content-rich, not a repealed
   placeholder. Save the HTML locally.

```bash
mkdir -p /tmp/probe
curl -sL -A "axiom-scraper/0.1" \
    "https://example.state.gov/statutes/1-1" \
    > /tmp/probe/sample.html
```

## 2. Scaffold the module

```
src/axiom_scrapers/jurisdictions/us_xx/statutes/
├── __init__.py
├── scrape.py
└── tests/
    ├── __init__.py
    ├── fixtures/
    │   └── sample.html             # saved from step 1
    └── test_parse.py
```

Remember `__init__.py` files — Python's import machinery needs them.

## 3. Write the scraper

```python
# src/axiom_scrapers/jurisdictions/us_xx/statutes/scrape.py
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date

from axiom_scrapers._common import (
    Scraper,
    Section,
    clean_text,
    http_get,
)


class StatutesScraper(Scraper[str]):
    # One scraper instance = one full scrape. `SectionRef` is a URL string.
    jurisdiction = "us-xx"
    doc_type = "statute"
    authority_code = "XXSC"
    author_id = "xx-legislature"
    author_name = "State of XX Legislature"
    author_url = "https://legislature.xx.gov"
    workers = 6

    def list_sections(self) -> Iterable[str]:
        # Walk the source; yield section URLs.
        ...

    def parse_section(self, url: str) -> Section | None:
        res = http_get(url)
        if res is None:
            return None
        return _parse_section_html(res.text(), self.generation_date)


def _parse_section_html(html: str, generation_date: date) -> Section | None:
    # Pure parse logic — testable against a fixture.
    m = re.search(r"...", html)
    if not m:
        return None
    ...
    return Section(
        jurisdiction="us-xx",
        doc_type="statute",
        authority_code="XXSC",
        work_number=work_number,
        citation=citation,
        heading=heading,
        body=body,
        author_id="xx-legislature",
        author_name="State of XX Legislature",
        author_url="https://legislature.xx.gov",
        generation_date=generation_date,
    )
```

## 4. Write offline parse tests

```python
# tests/test_parse.py
from datetime import date
from pathlib import Path
from axiom_scrapers.jurisdictions.us_xx.statutes.scrape import _parse_section_html

FIXTURES = Path(__file__).parent / "fixtures"


def test_parses_known_citation():
    html = (FIXTURES / "sample.html").read_text()
    sec = _parse_section_html(html, generation_date=date(2026, 4, 20))
    assert sec is not None
    assert sec.citation == "XX Stat. § 1-1"
    assert "expected body fragment" in sec.body


def test_no_header_returns_none():
    assert _parse_section_html("<html/>", date.today()) is None


def test_repealed_placeholder_returns_none():
    """Real sites publish 'Repealed.' placeholder pages — should skip."""
    ...
```

## 5. Register the scraper

Edit `src/axiom_scrapers/cli.py`:

```python
REGISTRY: dict[tuple[str, str], str] = {
    ...
    ("us-xx", "statutes"): "axiom_scrapers.jurisdictions.us_xx.statutes.scrape:StatutesScraper",
}
```

## 6. Validate

```bash
uv run pytest src/axiom_scrapers/jurisdictions/us_xx
uv run ruff check src/axiom_scrapers/jurisdictions/us_xx
uv run mypy src/axiom_scrapers/jurisdictions/us_xx
```

## 7. Run against production

```bash
uv run axiom-scrape --jurisdiction us-xx --doc-type statutes \
    --out ./out --limit 50    # smoke
uv run axiom-scrape --jurisdiction us-xx --doc-type statutes --out ./out  # full
```

## Tips

* **Don't log `Exception`.** Our base class wraps `parse_section` in a
  blanket `except Exception` and skips — never let one bad section crash
  a run.
* **Soft-fail HTTP.** `http_get` already returns `None` for 404/307/410
  and exhausted retries. Scrapers should just check and skip.
* **Strip source footers.** Most state sites append "Source: P.A. xxx"
  or "History:" lines. Trim them before emitting so the body is clean.
* **Pick a stable section identifier.** AKN's `FRBRnumber` value is the
  primary key Atlas uses. Pick whatever's unique within the jurisdiction
  — usually the state's canonical short cite (`1-339.1`, `244.010`,
  `35-155-2`).
