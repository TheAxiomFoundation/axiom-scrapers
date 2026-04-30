"""Test helpers shared across per-jurisdiction scrapers.

Kept in ``src/`` so ``axiom_scrapers._common.testing`` can be imported
from any test module. Not part of the public API; subject to change.

``install_fake_http`` is the main reason this exists. Per-state scrapers
each call ``axiom_scrapers._common.http.http_get`` through a
module-level alias, which means monkeypatching it requires reaching into
the specific ``scrape`` module. This helper centralizes the stub and the
fake-response wrapper so crawl-layer tests stay uniform across 19+
scrapers.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest


class FakeResponse:
    """Minimal stand-in for :class:`axiom_scrapers._common.http.FetchResult`.

    Only exposes :meth:`text` since that's all crawl-layer code ever
    calls on a fetch result. Accepts any ``fallback_encoding`` kwarg
    to match the fetch-result call shape.
    """

    def __init__(self, body: str) -> None:
        self._body = body

    def text(self, _fallback: str = "utf-8") -> str:
        return self._body


def install_fake_http(
    monkeypatch: pytest.MonkeyPatch,
    scrape_module: ModuleType,
    responses: dict[str, str | None],
    *,
    strict: bool = True,
) -> list[str]:
    """Replace ``scrape_module.http_get`` with a fake, record every URL fetched.

    Parameters
    ----------
    monkeypatch
        The pytest fixture; passed through for scoped cleanup.
    scrape_module
        The per-state scraper module whose ``http_get`` import should
        be stubbed (e.g. ``axiom_scrapers.jurisdictions.us_il.statutes.scrape``).
    responses
        Mapping of URL → body. A ``None`` value simulates a soft-fail
        (404/307/410 exhausted retries) so the real ``http_get`` would
        have returned ``None``.
    strict
        When ``True`` (default), any fetched URL that isn't in
        ``responses`` raises :class:`AssertionError`, surfacing
        unexpected crawls. Pass ``False`` to silently return ``None``
        for unlisted URLs.

    Returns
    -------
    A list that accumulates every fetched URL in call order. Tests
    assert on this list to verify the crawler visited the expected
    pages exactly once.
    """
    calls: list[str] = []

    def fake_http_get(url: str, **_kwargs: Any) -> FakeResponse | None:
        calls.append(url)
        if url not in responses:
            if strict:
                raise AssertionError(f"unexpected URL fetched: {url}")
            return None
        body = responses[url]
        return None if body is None else FakeResponse(body)

    monkeypatch.setattr(scrape_module, "http_get", fake_http_get)
    return calls
