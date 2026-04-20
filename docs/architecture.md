# Architecture

## Goals

1. **One place for scrapers** across all jurisdictions (US states, federal
   agencies, non-US eventually). Shared infrastructure, consistent output.
2. **Multiple document types** per jurisdiction — statutes, regulations,
   guidance, manuals, bills, rulemaking — each a separate module so they
   can evolve independently.
3. **Offline testability.** Every parser is unit-tested against saved HTML
   fixtures. No network calls in CI.
4. **Graceful decay.** Source sites change / go offline / rate-limit. Runs
   should skip dead sections, not crash.
5. **Consumer-agnostic output.** We emit Akoma Ntoso 3.0 XML. Atlas
   happens to be the first consumer; another project could ingest from
   the same tree.

## Layout

```
src/axiom_scrapers/
├── _common/                # Shared infrastructure
│   ├── http.py             # http_get: retries, soft-fail, 429 backoff
│   ├── text.py             # HTML → plain-text normalization
│   ├── akn.py              # Section dataclass + build_akn_xml
│   └── base.py             # Scraper abstract base class
├── jurisdictions/
│   └── {country}_{region}/
│       └── {doc_type}/
│           ├── scrape.py   # {State}{DocType}Scraper — subclasses Scraper
│           └── tests/
│               ├── test_parse.py
│               └── fixtures/
│                   └── *.html
├── cli.py                  # axiom-scrape dispatcher
└── __init__.py
tests/
├── test_cli.py
└── ...
```

### Jurisdiction naming

Directories use `{country}_{region}` with ISO 3166-style lowercase codes:

* `us_il` — Illinois
* `us_federal` — US federal sources (CFR, USC, IRS guidance)
* `uk_england` — England within the UK
* `ca_on` — Ontario, Canada

The corresponding AKN `FRBRcountry` value uses the dashed form `us-il`,
which matches Atlas's `jurisdiction` column. Convert via
`dir.replace("_", "-")`.

### Document types

Each jurisdiction can hold multiple doc-type subdirectories:

* `statutes/` — codified law (ILCS, NRS, USC)
* `regulations/` — administrative code (CFR, IL Admin Code)
* `guidance/` — sub-regulatory agency documents (IRS Rev. Procs.,
  SNAP policy memos)
* `manuals/` — operational handbooks (SNAP State Operations Handbook,
  CMS State Medicaid Manual)
* `bills/` — active legislation (not yet enacted)
* `rulemaking/` — proposed rules in comment periods

Each subdirectory is an independent scraper — its own
`list_sections()` / `parse_section()` implementation, its own fixtures,
its own tests.

## Scraper lifecycle

1. **Discovery** — `list_sections()` walks the upstream source and yields
   references (URLs, IDs, filesystem paths — whatever fits). The type
   parameter `SectionRef` on `Scraper` lets subclasses pick.
2. **Parse** — `parse_section(ref)` fetches + extracts one section's
   citation, heading, and body; returns `None` for repealed / missing /
   fetch-failed sections.
3. **Emit** — the base class serializes the `Section` to AKN XML and
   writes it under `out_root / relative_output_path(section)`.

Parallelism, progress logging, soft-fail on exceptions — all in the
base class. A new scraper is ~50-200 lines of state-specific regex.

## Testing boundary

* **Pure parse logic** — all per-state parse helpers live as module-level
  functions so they're testable against saved HTML. These tests never
  touch the network.
* **Base-class orchestration** — covered by `test_base.py` in `_common/`.
  Scrapers inherit this coverage for free.
* **Live-fetch integration** — intentionally not in CI. When a source
  site changes and parse fails, the saved fixtures will still pass; a
  manual re-run catches the drift.

## Output contract

See [`output-format.md`](output-format.md) for the AKN 3.0 shape Atlas
expects.

## Adding a scraper

See [`adding-a-scraper.md`](adding-a-scraper.md) for the step-by-step.

## Why monorepo, not per-jurisdiction repos

* Shared `_common/` library — we don't want to version-bump 50 repos when
  we change the retry policy.
* One CI signal for "is the scraping pipeline healthy?"
* `git subtree split` is a clean path to per-jurisdiction repos if we
  ever need that.
