"""HTTP fetcher with polite retries + soft-fail.

Design
------
Every per-state scraper calls ``http_get`` rather than raw urllib.
Putting retry + soft-fail here means:

* State-site flakiness (intermittent 500s, slow responses) is handled
  uniformly. Scrapers don't each reinvent backoff.
* "Section not found" (404/307/410) is a skip, not a crash — one
  repealed section shouldn't kill a multi-thousand-section walk.
* Rate-limit responses (429) get extra-long backoff.
* Connection resets / timeouts retry with exponential backoff.
* Every call is tagged with a consistent User-Agent so we're
  identifiable if a site operator needs to throttle us.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from typing import Callable

DEFAULT_UA = (
    "Mozilla/5.0 (compatible; axiom-scraper/0.1; "
    "+https://axiom-foundation.org; contact hello@axiom-foundation.org)"
)

# HTTP codes we treat as "section is gone, skip it" rather than "retry".
SKIPPABLE_STATUS = frozenset({307, 404, 410})


class FetchResult:
    """Wrap the bytes + resolved URL + encoding metadata from a successful fetch."""

    __slots__ = ("body", "url", "charset")

    def __init__(self, body: bytes, url: str, charset: str | None = None) -> None:
        self.body = body
        self.url = url
        self.charset = charset

    def text(self, fallback_encoding: str = "utf-8") -> str:
        """Return the body decoded as text.

        Prefers the response's declared charset when set; otherwise falls
        back to the caller's choice (most state sites are utf-8 but some
        are cp1252 — NV is notable).
        """
        encoding = self.charset or fallback_encoding
        return self.body.decode(encoding, errors="replace")


def http_get(
    url: str,
    *,
    retries: int = 5,
    timeout: float = 30.0,
    user_agent: str = DEFAULT_UA,
    sleeper: Callable[[float], None] = time.sleep,
    opener: Callable[[urllib.request.Request, float], "urllib.request.http.client.HTTPResponse"]
    | None = None,
) -> FetchResult | None:
    """Fetch a URL with retries + soft-fail. Returns None on give-up.

    Parameters
    ----------
    url
        Absolute URL to GET.
    retries
        Max attempts including the first try. Defaults to 5.
    timeout
        Per-attempt timeout in seconds.
    user_agent
        UA string sent on every request. Pinned to ``DEFAULT_UA`` so
        state operators can identify and throttle us if needed.
    sleeper
        Injected for tests; ``time.sleep`` in production.
    opener
        Injected for tests. Signature: ``(request, timeout) -> response``.
        Defaults to :func:`urllib.request.urlopen`.

    Returns
    -------
    FetchResult on success, ``None`` if the URL is missing
    (404/307/410), persistently unreachable, or exhausts all retries on
    transient errors.

    Notes
    -----
    Return-None-on-failure is intentional: one dead URL in a multi-
    thousand-section walk shouldn't kill the whole run. Callers should
    check for ``None`` and skip cleanly.
    """
    if opener is None:
        opener = urllib.request.urlopen  # type: ignore[assignment]

    last_exc: Exception | None = None
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})

    for attempt in range(1, retries + 1):
        try:
            with opener(req, timeout) as resp:  # type: ignore[misc]
                body = resp.read()
                charset: str | None = None
                content_type = resp.headers.get("Content-Type", "")
                if "charset=" in content_type.lower():
                    charset = content_type.lower().split("charset=", 1)[1].split(";")[0].strip()
                return FetchResult(body=body, url=resp.url, charset=charset)
        except urllib.error.HTTPError as exc:
            # Skippable statuses mean "the URL doesn't have content" — no retry.
            if exc.code in SKIPPABLE_STATUS:
                return None
            # 429: back off more aggressively (aggressive retry deepens the block).
            if exc.code == 429:
                if attempt < retries:
                    sleeper(min(60.0, 10.0 * attempt))
                    continue
            last_exc = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_exc = exc
        if attempt < retries:
            sleeper(min(8.0, 2.0**attempt))

    # Exhausted retries. Caller's log will include the URL; we just return None.
    _ = last_exc  # retained for future diagnostic hook
    return None
