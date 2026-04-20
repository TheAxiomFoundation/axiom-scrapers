"""``axiom-scrape`` entry point.

Resolves a ``(jurisdiction, doc_type)`` pair to a concrete scraper
subclass via the :data:`REGISTRY`, then runs it.

CLI surface is intentionally thin — full config lives on the scraper
class itself (workers, auth, URL patterns). Flags here cover only
cross-cutting run options.

Example::

    uv run axiom-scrape --jurisdiction us-il --doc-type statutes --out ./out
    uv run axiom-scrape --list
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from ._common import Scraper

#: (jurisdiction, doc_type) → fully-qualified scraper class path.
#: Adding a new scraper is one line here plus its module + tests.
REGISTRY: dict[tuple[str, str], str] = {
    ("us-il", "statutes"): "axiom_scrapers.jurisdictions.us_il.statutes.scrape:ILCSStatutesScraper",
}


def _load(path: str) -> type[Scraper]:
    """Import ``module:attr`` and return the attribute."""
    mod_name, attr = path.split(":", 1)
    mod = importlib.import_module(mod_name)
    cls = getattr(mod, attr)
    if not isinstance(cls, type) or not issubclass(cls, Scraper):
        raise TypeError(f"{path} is not a Scraper subclass")
    return cls


def _print_registry() -> None:
    print("Registered scrapers:")
    for (juris, dtype), path in sorted(REGISTRY.items()):
        print(f"  {juris:12s} {dtype:12s}  {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--jurisdiction",
        help="Jurisdiction slug, e.g. 'us-il', 'us-federal'. Use --list to enumerate.",
    )
    parser.add_argument(
        "--doc-type",
        default="statutes",
        help="One of: statutes, regulations, guidance, manuals, bills, rulemaking.",
    )
    parser.add_argument("--out", type=Path, default=Path("./out"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print registered scrapers and exit.",
    )
    args = parser.parse_args(argv)

    if args.list:
        _print_registry()
        return 0

    if not args.jurisdiction:
        parser.error("--jurisdiction is required (use --list to enumerate)")

    key = (args.jurisdiction, args.doc_type)
    if key not in REGISTRY:
        print(
            f"ERROR: no scraper registered for {key!r}. "
            f"Run with --list to see what's available.",
            file=sys.stderr,
        )
        return 2

    cls = _load(REGISTRY[key])
    result = cls().run(args.out, limit=args.limit)
    return 0 if result.written > 0 else 1


if __name__ == "__main__":  # pragma: no cover — entrypoint
    raise SystemExit(main())
