"""Tests for the AKN 3.0 XML builder."""

from datetime import date
from xml.etree import ElementTree as ET

import pytest

from axiom_scrapers._common.akn import AKN_NS, Section, _safe_eid, build_akn_xml


def make_section(**overrides: object) -> Section:
    defaults: dict[str, object] = {
        "jurisdiction": "us-il",
        "doc_type": "statute",
        "authority_code": "ILCS",
        "work_number": "35-155-2",
        "citation": "35 ILCS 155/2",
        "heading": "Definitions",
        "body": "As used in this Act:\n\n\"Renting\" means any transfer.",
        "author_id": "il-legislature",
        "author_name": "Illinois General Assembly",
        "author_url": "https://www.ilga.gov",
        "generation_date": date(2026, 4, 20),
    }
    defaults.update(overrides)
    return Section(**defaults)  # type: ignore[arg-type]


class TestBuildAknXml:
    def test_output_is_valid_xml(self) -> None:
        xml = build_akn_xml(make_section())
        # Parses without error.
        root = ET.fromstring(xml)
        assert root.tag == f"{{{AKN_NS}}}akomaNtoso"

    def test_frbr_number_is_the_work_number(self) -> None:
        xml = build_akn_xml(make_section(work_number="9A-33-5"))
        root = ET.fromstring(xml)
        el = root.find(
            f".//{{{AKN_NS}}}FRBRWork/{{{AKN_NS}}}FRBRnumber"
        )
        assert el is not None
        assert el.get("value") == "9A-33-5"

    def test_frbr_name_is_the_authority_code(self) -> None:
        xml = build_akn_xml(make_section(authority_code="NRS"))
        root = ET.fromstring(xml)
        el = root.find(f".//{{{AKN_NS}}}FRBRWork/{{{AKN_NS}}}FRBRname")
        assert el is not None
        assert el.get("value") == "NRS"

    def test_frbr_country_is_jurisdiction(self) -> None:
        xml = build_akn_xml(make_section(jurisdiction="us-ny"))
        root = ET.fromstring(xml)
        el = root.find(f".//{{{AKN_NS}}}FRBRcountry")
        assert el is not None
        assert el.get("value") == "us-ny"

    def test_no_enacted_frbr_date(self) -> None:
        """Regression — we only emit publication + generation, never enacted.

        The work-level 'enacted' date is unknown at scrape time; putting
        the scrape date there would be wrong.
        """
        xml = build_akn_xml(make_section())
        root = ET.fromstring(xml)
        dates = root.findall(f".//{{{AKN_NS}}}FRBRdate")
        names = {d.get("name") for d in dates}
        assert "enacted" not in names
        assert "publication" in names
        assert "generation" in names

    def test_citation_appears_in_num(self) -> None:
        xml = build_akn_xml(make_section(citation="R.C. § 5747.01"))
        root = ET.fromstring(xml)
        num = root.find(
            f".//{{{AKN_NS}}}section/{{{AKN_NS}}}num"
        )
        assert num is not None
        assert num.text == "R.C. § 5747.01"

    def test_heading_appears_in_heading(self) -> None:
        xml = build_akn_xml(make_section(heading="Short title of title"))
        root = ET.fromstring(xml)
        heading = root.find(
            f".//{{{AKN_NS}}}section/{{{AKN_NS}}}heading"
        )
        assert heading is not None
        assert heading.text == "Short title of title"

    def test_empty_heading_falls_back_to_section_number(self) -> None:
        xml = build_akn_xml(make_section(heading="", work_number="606"))
        root = ET.fromstring(xml)
        heading = root.find(
            f".//{{{AKN_NS}}}section/{{{AKN_NS}}}heading"
        )
        assert heading is not None
        assert heading.text == "Section 606"

    def test_body_splits_on_blank_lines_into_ps(self) -> None:
        body = "Para one.\n\nPara two.\n\nPara three."
        xml = build_akn_xml(make_section(body=body))
        root = ET.fromstring(xml)
        ps = root.findall(
            f".//{{{AKN_NS}}}section/{{{AKN_NS}}}content/{{{AKN_NS}}}p"
        )
        assert len(ps) == 3
        assert ps[0].text == "Para one."
        assert ps[1].text == "Para two."
        assert ps[2].text == "Para three."

    def test_empty_body_emits_empty_p(self) -> None:
        xml = build_akn_xml(make_section(body=""))
        root = ET.fromstring(xml)
        ps = root.findall(
            f".//{{{AKN_NS}}}section/{{{AKN_NS}}}content/{{{AKN_NS}}}p"
        )
        # Single <p/> self-closed.
        assert len(ps) == 1

    def test_xml_entities_escaped_in_body(self) -> None:
        xml = build_akn_xml(
            make_section(body="A < B and C > D and E & F.")
        )
        root = ET.fromstring(xml)
        p = root.find(
            f".//{{{AKN_NS}}}section/{{{AKN_NS}}}content/{{{AKN_NS}}}p"
        )
        assert p is not None
        assert p.text == "A < B and C > D and E & F."

    def test_xml_entities_escaped_in_citation(self) -> None:
        xml = build_akn_xml(make_section(citation='"R.C. 1.01"'))
        # No crash, parses, and the citation round-trips.
        root = ET.fromstring(xml)
        num = root.find(
            f".//{{{AKN_NS}}}section/{{{AKN_NS}}}num"
        )
        assert num is not None
        assert num.text == '"R.C. 1.01"'

    def test_frbr_uris_use_jurisdiction_and_authority_lowercased(self) -> None:
        xml = build_akn_xml(
            make_section(
                jurisdiction="us-nv",
                authority_code="NRS",
                work_number="244.010",
            )
        )
        root = ET.fromstring(xml)
        this = root.find(
            f".//{{{AKN_NS}}}FRBRWork/{{{AKN_NS}}}FRBRthis"
        )
        assert this is not None
        assert this.get("value") == "/akn/us-nv/act/nrs/244.010"

    def test_expression_manifestation_dated_with_generation_date(self) -> None:
        xml = build_akn_xml(
            make_section(generation_date=date(2026, 1, 15))
        )
        root = ET.fromstring(xml)
        exp = root.find(
            f".//{{{AKN_NS}}}FRBRExpression/{{{AKN_NS}}}FRBRdate"
        )
        assert exp is not None
        assert exp.get("date") == "2026-01-15"
        assert exp.get("name") == "publication"

        mani = root.find(
            f".//{{{AKN_NS}}}FRBRManifestation/{{{AKN_NS}}}FRBRdate"
        )
        assert mani is not None
        assert mani.get("date") == "2026-01-15"
        assert mani.get("name") == "generation"

    def test_author_organization_block_emitted(self) -> None:
        xml = build_akn_xml(
            make_section(
                author_id="nv-legislature",
                author_name="Nevada Legislature",
                author_url="https://www.leg.state.nv.us",
            )
        )
        root = ET.fromstring(xml)
        orgs = root.findall(f".//{{{AKN_NS}}}TLCOrganization")
        ids = {o.get("eId") for o in orgs}
        assert "nv-legislature" in ids
        assert "axiom" in ids


class TestSafeEid:
    @pytest.mark.parametrize(
        "number, expected",
        [
            ("101", "sec_101"),
            ("1-339.1", "sec_1_339_1"),
            ("244.010", "sec_244_010"),
            ("28:9-316", "sec_28_9_316"),
            ("35-155-2.1", "sec_35_155_2_1"),
        ],
    )
    def test_dots_dashes_colons_become_underscores(
        self, number: str, expected: str
    ) -> None:
        assert _safe_eid(number) == expected

    def test_letters_digits_preserved(self) -> None:
        assert _safe_eid("1004A") == "sec_1004A"

    def test_empty_string_just_returns_prefix(self) -> None:
        assert _safe_eid("") == "sec_"
