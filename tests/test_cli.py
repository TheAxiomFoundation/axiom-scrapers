"""Tests for the axiom-scrape CLI dispatcher."""

from __future__ import annotations

from pathlib import Path

import pytest

from axiom_scrapers import cli
from axiom_scrapers._common import Scraper


class TestRegistry:
    def test_registry_values_resolve_to_scraper_subclasses(self) -> None:
        for path in cli.REGISTRY.values():
            klass = cli._load(path)
            assert issubclass(klass, Scraper)

    def test_load_rejects_non_scraper(self) -> None:
        with pytest.raises(TypeError, match="not a Scraper subclass"):
            cli._load("builtins:object")


class TestMain:
    def test_list_flag_prints_registry(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.main(["--list"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Registered scrapers:" in out
        assert "us-il" in out

    def test_missing_jurisdiction_errors(self) -> None:
        with pytest.raises(SystemExit) as exc:
            cli.main([])
        assert exc.value.code == 2

    def test_unknown_scraper_exits_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.main(["--jurisdiction", "us-xx", "--doc-type", "statutes"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "no scraper registered" in err

    def test_dispatch_runs_scraper(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """End-to-end: --jurisdiction dispatches to the scraper class.

        We monkeypatch REGISTRY to a stub so we don't hit a live site.
        """
        from axiom_scrapers._common.akn import Section
        from datetime import date

        class StubScraper(Scraper[str]):
            jurisdiction = "us-stub"
            doc_type = "statute"
            authority_code = "STUB"
            author_id = "stub-auth"
            author_name = "Stub"
            author_url = "https://example.test"

            def list_sections(self) -> list[str]:
                return ["1.01"]

            def parse_section(self, ref: str) -> Section | None:
                return Section(
                    jurisdiction=self.jurisdiction,
                    doc_type=self.doc_type,
                    authority_code=self.authority_code,
                    work_number=ref,
                    citation=f"STUB {ref}",
                    heading="Test",
                    body="Test body.",
                    author_id=self.author_id,
                    author_name=self.author_name,
                    author_url=self.author_url,
                    generation_date=date.today(),
                )

        # Register the stub and point the loader at it.
        monkeypatch.setitem(
            cli.REGISTRY,
            ("us-stub", "statutes"),
            "tests.test_cli:_STUB_SCRAPER_REF",
        )
        # Also patch _load to bypass import machinery for the stub.
        real_load = cli._load

        def fake_load(path: str) -> type[Scraper]:
            if path == "tests.test_cli:_STUB_SCRAPER_REF":
                return StubScraper
            return real_load(path)

        monkeypatch.setattr(cli, "_load", fake_load)

        rc = cli.main(
            [
                "--jurisdiction",
                "us-stub",
                "--doc-type",
                "statutes",
                "--out",
                str(tmp_path),
            ]
        )
        assert rc == 0
        assert (tmp_path / "us-stub/statute/1.01.xml").exists()
