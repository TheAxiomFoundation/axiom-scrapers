# axiom-scrapers

Scrapers for global statutes, regulations, bills, and rulemaking. Each scraper fetches
from an authoritative upstream source (state legislature website, eCFR API, etc.) and
emits [Akoma Ntoso 3.0](https://www.oasis-open.org/committees/download.php/59858/akn-core-v1.0-cos01-part1.pdf)
XML files. The downstream consumer is [Atlas](https://github.com/TheAxiomFoundation/atlas),
which ingests the XML into Postgres for the Atlas viewer and RAC encoding pipeline.

## Layout

```
src/axiom_scrapers/
├── _common/               # Shared infrastructure (http, akn-xml, text, base class)
├── jurisdictions/
│   └── {country}_{region}/
│       └── {doc_type}/    # statutes, regulations, guidance, bills, rulemaking, manuals
│           ├── scrape.py
│           └── tests/
│               ├── test_parse.py
│               └── fixtures/
└── cli.py                 # axiom-scrape CLI
```

Doc types we support today: `statutes`, `regulations`, `guidance`, `manuals`.
Doc types planned: `bills`, `rulemaking` (proposed rules in comment periods).

## Running

```bash
# One jurisdiction, one doc type
uv run axiom-scrape --jurisdiction us-il --doc-type statutes --out ./out

# All known jurisdictions for a doc type
uv run axiom-scrape --doc-type statutes --all --out ./out

# Dry-run (parse one section, don't write)
uv run axiom-scrape --jurisdiction us-il --doc-type statutes --dry-run
```

## Adding a new scraper

See [`docs/adding-a-scraper.md`](docs/adding-a-scraper.md). TL;DR: subclass
`axiom_scrapers._common.base.Scraper`, implement `list_sections()` and
`parse_section()`, write fixture-based parse tests.

## Development

```bash
uv sync
uv run pytest           # full suite + coverage
uv run ruff check .
uv run mypy src/
```

Coverage floor is 85%. CI enforces it.

## Architecture choices

- **Monorepo, not per-state repos.** One place for shared code + CI health; easier to
  refactor all scrapers at once. If a state ever needs its own release cadence,
  `git subtree split` cleanly extracts it.
- **Offline-first tests.** Parse logic is unit-tested against saved HTML fixtures,
  not live fetches. Fetches are covered by a separate `test_fetch` marker that runs
  weekly on CI, not on every PR.
- **AKN 3.0 output.** Standardized across all scrapers so Atlas ingest is uniform.
  See [`docs/output-format.md`](docs/output-format.md).

## Migration status

The scrapers currently live in two places — `axiom-scrapers` (new) and
`atlas/scripts/scrape_*.py` (legacy). Both emit the same AKN shape and feed the same
Atlas ingester, so there's no urgency to the migration, but the legacy scrapers
should move one at a time.

Migrated:

| Jurisdiction | Doc type  | Status   |
| ------------ | --------- | -------- |
| us-il        | statutes  | migrated |

Queued for migration (still live in `atlas/scripts/`):

- us-nv, us-nc, us-az, us-pa, us-or, us-mn, us-oh, us-mo, us-mt,
  us-de, us-ne, us-nh, us-ri, us-wa, us-sc, us-vt, us-in, us-ky
- us-federal (CFR via eCFR API — `atlas/scripts/ingest_cfr_parts.py`)
- us-federal (IRS guidance — `atlas/scripts/ingest_irs_guidance.py` if added)

Each is a ~1-hour port: copy the parse regex, rewrite around
`Scraper[SectionRef]`, save a real HTML fixture, write 5-10 parse
tests. See [`docs/adding-a-scraper.md`](docs/adding-a-scraper.md).
