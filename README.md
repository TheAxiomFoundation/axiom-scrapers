# axiom-scrapers

Scrapers for global statutes, regulations, bills, and rulemaking. Each scraper fetches
from an authoritative upstream source (state legislature website, eCFR API, etc.) and
emits local source-section text plus sidecar metadata as an ingest intermediate.
The downstream consumer is
[axiom-corpus](https://github.com/TheAxiomFoundation/axiom-corpus), which ingests
that scratch output into Postgres for the Axiom app and RuleSpec encoding
pipeline. Generated scrape output stays out of Git and R2.

## Layout

```
src/axiom_scrapers/
├── _common/               # Shared infrastructure (http, source output, text, base class)
├── jurisdictions/
│   └── {country}_{region}/
│       └── {doc_type_dir}/ # statutes, regulations, guidance, bills, rulemaking, manuals
│           ├── scrape.py
│           └── tests/
│               ├── test_parse.py
│               └── fixtures/
└── cli.py                 # axiom-scrape CLI
```

`doc_type` values are singular (`statute`, `regulation`, `guidance`, `manual`, `bill`,
`rulemaking`) — they describe one document, not a collection.

## Running

```bash
# List every registered scraper
uv run axiom-scrape --list

# Scrape one jurisdiction
uv run axiom-scrape --jurisdiction us-il --doc-type statute --out ./out

# Limit to the first N sections (useful for smoke tests)
uv run axiom-scrape --jurisdiction us-il --doc-type statute --limit 5 --out ./out
```

## Adding a new scraper

See [`docs/adding-a-scraper.md`](docs/adding-a-scraper.md). TL;DR: subclass
`axiom_scrapers._common.base.Scraper`, implement `list_sections()` and
`parse_section()`, write fixture-based parse tests, add a line to the CLI
`REGISTRY` dict.

## Development

```bash
uv sync --dev
uv run pytest           # full suite + coverage
uv run ruff check .
uv run mypy src/
```

Coverage floor is 75%. The remaining 25% is primarily the network-crawling
helpers (directory listings, TOC walks) that would need a mock `http_get`
to cover. [`docs/architecture.md`](docs/architecture.md) has the plan to raise
the floor as those tests land.

## Architecture choices

- **Monorepo, not per-state repos.** One place for shared code + CI health; easier to
  refactor all scrapers at once. If a state ever needs its own release cadence,
  `git subtree split` cleanly extracts it.
- **Offline-first tests.** Parse logic is unit-tested against saved HTML fixtures,
  not live fetches. The `_common.http` layer handles retries, rate-limit backoff, and
  soft-fail for missing sections (404/307/410) so per-state scrapers can stay thin.
- **Source-section intermediate output.** Standardized across all scrapers so
  Axiom corpus ingest is uniform without making generated scrape output a
  persisted artifact.
  See [`docs/output-format.md`](docs/output-format.md).

## Jurisdictions covered

19 US states as of 2026-04-20. Run `uv run axiom-scrape --list` for the current set.

| State | Authority code | Source                                     |
| ----- | -------------- | ------------------------------------------ |
| us-az | A.R.S.         | azleg.gov/ars                              |
| us-de | Del. C.        | delcode.delaware.gov                       |
| us-il | ILCS           | ilga.gov/ftp/ILCS                          |
| us-in | IC             | iga.in.gov/ic                              |
| us-ky | KRS            | apps.legislature.ky.gov (PDFs via poppler) |
| us-mn | Minn. Stat.    | revisor.mn.gov/statutes                    |
| us-mo | RSMo           | revisor.mo.gov                             |
| us-mt | MCA            | leg.mt.gov/bills/mca                       |
| us-nc | G.S.           | ncleg.gov/EnactedLegislation               |
| us-ne | Neb. Rev. Stat.| nebraskalegislature.gov/laws               |
| us-nh | RSA            | gencourt.state.nh.us/rsa                   |
| us-nv | NRS            | leg.state.nv.us/NRS                        |
| us-oh | R.C.           | codes.ohio.gov/ohio-revised-code           |
| us-or | ORS            | oregonlegislature.gov/bills_laws/ors       |
| us-pa | Pa. C.S.       | palrb.us                                   |
| us-ri | RIGL           | webserver.rilegislature.gov/Statutes       |
| us-sc | S.C. Code      | scstatehouse.gov/code                      |
| us-vt | V.S.A.         | legislature.vermont.gov/statutes           |
| us-wa | RCW            | app.leg.wa.gov/RCW                         |

Federal CFR and IRS guidance are currently ingested directly by
[`axiom-corpus/scripts/ingest_cfr_parts.py`](https://github.com/TheAxiomFoundation/axiom-corpus/blob/main/scripts/ingest_cfr_parts.py)
and related ingesters; porting them into this repo is tracked separately.

## Downstream

Every scraper writes paired `{section}.txt` and `{section}.meta.yaml` files under
`{out}/{jurisdiction}/{doc_type_dir}/.../`, matching the shape the Axiom corpus
ingester expects. The two repos are kept deliberately decoupled:
axiom-scrapers produces local source-section artifacts, Axiom ingests them.
Neither imports from the other. Treat the output tree as local scratch space; do
not commit it or upload it to R2.
