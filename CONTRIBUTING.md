# Contributing

Use short-lived branches off `main` and open a pull request back to `main`.
Keep PRs focused, describe the checks you ran, and wait for CI before merging.

## Pull request flow

1. Create a branch from an up-to-date `main`.
2. Make the smallest coherent change and include fixture-backed tests for
   scraper behavior changes.
3. Add a Towncrier fragment under `changelog.d/` unless the PR is docs,
   tests-only, or otherwise has no user-visible release note.
4. Open the PR to `main` and complete the PR template.
5. Merge after review approval and green CI.

Towncrier fragment categories are `breaking`, `added`, `changed`, `fixed`, and
`removed`. Name fragments descriptively, for example
`changelog.d/us-il-parser.fixed.md`.

## Local checks

CI runs the changelog draft, Ruff, mypy, and the offline pytest suite across the
supported Python versions. Run the relevant subset locally before opening the PR:

```bash
uv sync --dev
uv run python -m towncrier build --draft --version 0.0.0
uv run ruff check .
uv run mypy src/
uv run pytest
```

## Repo notes

- Tests should stay offline-first and use saved fixtures instead of live
  upstream requests.
- Scrape output under `out/` or other scratch directories is generated data; do
  not commit it.
- New jurisdiction work should follow `docs/adding-a-scraper.md` and keep the
  CLI registry in sync with the scraper implementation.
