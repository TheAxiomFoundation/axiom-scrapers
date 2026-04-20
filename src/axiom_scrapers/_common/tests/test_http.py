"""Tests for the shared HTTP fetcher."""

from __future__ import annotations

import io
import urllib.error
from typing import Any

import pytest

from axiom_scrapers._common.http import DEFAULT_UA, FetchResult, SKIPPABLE_STATUS, http_get


class _FakeResponse:
    """Minimal context-manager mimicking ``urllib.request.urlopen`` result."""

    def __init__(self, body: bytes, status: int = 200, headers: dict[str, str] | None = None, url: str | None = None) -> None:
        self._body = body
        self.status = status
        self.headers = headers or {}
        self.url = url or "https://example.test/"

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _make_opener(responses: list[Any]):
    """Return a fake ``opener`` callable that replays `responses` in order.

    Each entry is either a ``_FakeResponse`` (returned) or an ``Exception``
    instance (raised).
    """
    calls: list[str] = []

    def opener(req: Any, timeout: float) -> _FakeResponse:  # noqa: ARG001
        calls.append(req.full_url)
        if not responses:
            raise RuntimeError("opener called too many times")
        nxt = responses.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt  # type: ignore[return-value]

    opener.calls = calls  # type: ignore[attr-defined]
    return opener


def _noop_sleep(_: float) -> None:  # pragma: no cover — just a placeholder
    pass


class TestHttpGetSuccess:
    def test_returns_body_on_200(self) -> None:
        opener = _make_opener([_FakeResponse(b"hello", headers={"Content-Type": "text/html; charset=utf-8"})])
        got = http_get("https://x.test/", opener=opener, sleeper=_noop_sleep)
        assert got is not None
        assert got.body == b"hello"
        assert got.charset == "utf-8"
        assert got.text() == "hello"

    def test_text_uses_fallback_encoding_when_unspecified(self) -> None:
        opener = _make_opener([_FakeResponse("café".encode("cp1252"), headers={})])
        got = http_get("https://nv.test/", opener=opener, sleeper=_noop_sleep)
        assert got is not None
        assert got.charset is None
        assert got.text("cp1252") == "café"

    def test_resolved_url_exposed(self) -> None:
        """Redirects set resp.url to the final URL — we surface it."""
        opener = _make_opener([_FakeResponse(b"", url="https://x.test/final")])
        got = http_get("https://x.test/start", opener=opener, sleeper=_noop_sleep)
        assert got is not None
        assert got.url == "https://x.test/final"

    def test_user_agent_defaults_to_axiom(self) -> None:
        captured_ua: list[str] = []

        def opener(req: Any, timeout: float) -> _FakeResponse:  # noqa: ARG001
            captured_ua.append(req.get_header("User-agent"))
            return _FakeResponse(b"ok")

        http_get("https://x.test/", opener=opener, sleeper=_noop_sleep)
        assert captured_ua == [DEFAULT_UA]

    def test_custom_user_agent_propagated(self) -> None:
        captured: list[str] = []

        def opener(req: Any, timeout: float) -> _FakeResponse:  # noqa: ARG001
            captured.append(req.get_header("User-agent"))
            return _FakeResponse(b"ok")

        http_get("https://x.test/", user_agent="Custom/1.0", opener=opener, sleeper=_noop_sleep)
        assert captured == ["Custom/1.0"]


class TestHttpGetSkippable:
    @pytest.mark.parametrize("status", sorted(SKIPPABLE_STATUS))
    def test_skippable_status_returns_none_no_retry(self, status: int) -> None:
        err = urllib.error.HTTPError(
            url="https://x.test/", code=status, msg="gone", hdrs=None, fp=io.BytesIO(b"")
        )
        opener = _make_opener([err])
        got = http_get("https://x.test/", opener=opener, sleeper=_noop_sleep)
        assert got is None
        # One call — no retry after a skippable status.
        assert len(opener.calls) == 1  # type: ignore[attr-defined]


class TestHttpGetRetries:
    def test_retries_on_5xx_then_succeeds(self) -> None:
        err = urllib.error.HTTPError(
            url="https://x.test/", code=502, msg="bad gateway", hdrs=None, fp=io.BytesIO(b"")
        )
        opener = _make_opener([err, err, _FakeResponse(b"ok")])
        sleeps: list[float] = []
        got = http_get(
            "https://x.test/", opener=opener, sleeper=sleeps.append
        )
        assert got is not None
        assert got.body == b"ok"
        # Two 502s before success — two backoff sleeps.
        assert len(sleeps) == 2
        assert sleeps == [2.0, 4.0]  # 2**1, 2**2

    def test_retries_on_timeout_then_succeeds(self) -> None:
        opener = _make_opener([TimeoutError("timed out"), _FakeResponse(b"ok")])
        got = http_get("https://x.test/", opener=opener, sleeper=_noop_sleep)
        assert got is not None
        assert got.body == b"ok"

    def test_retries_on_connection_reset(self) -> None:
        opener = _make_opener([ConnectionResetError("reset"), _FakeResponse(b"ok")])
        got = http_get("https://x.test/", opener=opener, sleeper=_noop_sleep)
        assert got is not None

    def test_soft_fails_after_retries_exhausted(self) -> None:
        err = urllib.error.HTTPError(
            url="https://x.test/", code=500, msg="boom", hdrs=None, fp=io.BytesIO(b"")
        )
        opener = _make_opener([err] * 5)
        got = http_get("https://x.test/", retries=5, opener=opener, sleeper=_noop_sleep)
        assert got is None
        # 5 attempts total, 4 backoff sleeps between them.
        assert len(opener.calls) == 5  # type: ignore[attr-defined]

    def test_429_uses_longer_backoff(self) -> None:
        err = urllib.error.HTTPError(
            url="https://x.test/", code=429, msg="rate limited", hdrs=None, fp=io.BytesIO(b"")
        )
        opener = _make_opener([err, _FakeResponse(b"ok")])
        sleeps: list[float] = []
        got = http_get("https://x.test/", opener=opener, sleeper=sleeps.append)
        assert got is not None
        # 429 backoff is 10*attempt — much longer than the 2**attempt default.
        assert sleeps == [10.0]

    def test_429_caps_at_60_seconds(self) -> None:
        err = urllib.error.HTTPError(
            url="https://x.test/", code=429, msg="rate limited", hdrs=None, fp=io.BytesIO(b"")
        )
        opener = _make_opener([err] * 10 + [_FakeResponse(b"ok")])
        sleeps: list[float] = []
        http_get("https://x.test/", retries=10, opener=opener, sleeper=sleeps.append)
        assert all(s <= 60.0 for s in sleeps)


class TestFetchResult:
    def test_text_falls_back_to_utf8(self) -> None:
        r = FetchResult(body=b"hello", url="https://x.test/")
        assert r.text() == "hello"

    def test_text_honors_response_charset(self) -> None:
        r = FetchResult(body="café".encode("cp1252"), url="https://x.test/", charset="cp1252")
        assert r.text() == "café"
