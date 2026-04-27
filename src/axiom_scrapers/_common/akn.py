"""Akoma Ntoso 3.0 document builder.

Centralizes the AKN XML template so every scraper emits the same shape.
Atlas's ``ingest_state_laws.py`` expects this exact structure — it
looks up ``<FRBRnumber>`` for the section id and ``.//akn:body//akn:section``
for the heading + content.

Authored deliberately — no third-party AKN library. Keeping it in-house
means we can adapt to Atlas ingestion tweaks without pulling in a
dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from xml.sax.saxutils import escape as xml_escape

from .text import split_paragraphs

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# XML 1.0 forbids most C0 control characters (everything below U+0020
# except tab / LF / CR). Upstream sources occasionally leak these —
# Federal Register PDF→text conversions embed NULL bytes (\x00), and
# some state HTML carries stray form-feeds. Strip them before emitting
# so the resulting AKN document is well-formed.
_INVALID_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def strip_invalid_xml_chars(s: str) -> str:
    """Remove C0 control characters that XML 1.0 forbids."""
    return _INVALID_XML_CHARS.sub("", s)


@dataclass(frozen=True)
class Section:
    """One scraped section ready for AKN emission.

    Attributes
    ----------
    jurisdiction
        Full jurisdiction slug, e.g. ``"us-il"``, ``"us-federal"``,
        ``"uk"``. Used in FRBR URIs.
    doc_type
        The Atlas doc_type this section rolls up under —
        ``"statute"``, ``"regulation"``, ``"guidance"``, ``"manual"``.
        Different authorities will emit different sets.
    authority_code
        Short abbreviation of the authoritative citation format
        (``"ILCS"``, ``"RCW"``, ``"CFR"``, ``"USC"``, ``"NRS"``, etc.).
        Stored in ``<FRBRname>`` so downstream can render short cites.
    work_number
        Identifier unique to this work within its jurisdiction. For a
        state statute section it's typically the section id (or
        ``{title}-{section}``); for a CFR section it's ``{title}.{part}.{sec}``.
    citation
        Human-readable citation text (``"ILCS 35/155/2"``, ``"R.C. § 5747.01"``,
        ``"35 C.F.R. § 273.9"``). Goes into ``<num>``.
    heading
        The short description / section title. Goes into ``<heading>``.
    body
        The body text, with paragraphs separated by ``\\n\\n``. Each
        paragraph emits as a ``<p>``.
    author_id
        Short id for the legislative/regulatory author, e.g.
        ``"il-legislature"``, ``"nv-legislature"``, ``"us-ecfr"``.
    author_name
        Display name, e.g. ``"Illinois General Assembly"``.
    author_url
        URL to the authoritative source, e.g. ``"https://www.ilga.gov"``.
    generation_date
        When this scrape ran. Stored in Expression/Manifestation
        FRBRdates (``publication`` / ``generation``) so we can
        diff later runs. Not ``enacted`` — we don't know that.
    """

    jurisdiction: str
    doc_type: str
    authority_code: str
    work_number: str
    citation: str
    heading: str
    body: str
    author_id: str
    author_name: str
    author_url: str
    generation_date: date


def build_akn_xml(section: Section) -> str:
    """Render a :class:`Section` into an Akoma Ntoso 3.0 document.

    The shape is stable — Atlas's ingester keys on ``<FRBRnumber>`` and
    ``<section>``'s first ``<num>`` and ``<heading>`` children.
    """
    paras = split_paragraphs(strip_invalid_xml_chars(section.body))
    paras_xml = (
        "\n            ".join(f"<p>{xml_escape(p)}</p>" for p in paras)
        if paras
        else "<p/>"
    )
    gen = section.generation_date.isoformat()

    # Canonical FRBR paths mirror the existing rules-us-* repos so Atlas's
    # ingester doesn't need per-author logic.
    jurisdiction = section.jurisdiction
    authority = section.authority_code.lower()
    number = section.work_number
    work_uri = f"/akn/{jurisdiction}/act/{authority}/{number}"
    exp_uri = f"{work_uri}/eng@{gen}"
    manifestation_uri = f"{exp_uri}/main.xml"

    work_uri_xml = xml_escape(work_uri, {'"': "&quot;"})
    exp_uri_xml = xml_escape(exp_uri, {'"': "&quot;"})
    manifestation_uri_xml = xml_escape(manifestation_uri, {'"': "&quot;"})
    author_url_xml = xml_escape(section.author_url, {'"': "&quot;"})

    eid = _safe_eid(number)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{AKN_NS}">
  <act name="section">
    <meta>
      <identification source="#axiom">
        <FRBRWork>
          <FRBRthis value="{work_uri_xml}"/>
          <FRBRuri value="{work_uri_xml}"/>
          <FRBRauthor href="#{section.author_id}"/>
          <FRBRcountry value="{jurisdiction}"/>
          <FRBRnumber value="{xml_escape(number, {'"': "&quot;"})}"/>
          <FRBRname value="{xml_escape(section.authority_code, {'"': "&quot;"})}"/>
        </FRBRWork>
        <FRBRExpression>
          <FRBRthis value="{exp_uri_xml}"/>
          <FRBRuri value="{exp_uri_xml}"/>
          <FRBRdate date="{gen}" name="publication"/>
          <FRBRauthor href="#axiom"/>
          <FRBRlanguage language="eng"/>
        </FRBRExpression>
        <FRBRManifestation>
          <FRBRthis value="{manifestation_uri_xml}"/>
          <FRBRuri value="{manifestation_uri_xml}"/>
          <FRBRdate date="{gen}" name="generation"/>
          <FRBRauthor href="#axiom"/>
        </FRBRManifestation>
      </identification>
      <references source="#axiom">
        <TLCOrganization eId="{section.author_id}" href="{author_url_xml}" showAs="{xml_escape(section.author_name, {'"': "&quot;"})}"/>
        <TLCOrganization eId="axiom" href="https://axiom-foundation.org" showAs="Axiom Foundation"/>
      </references>
    </meta>
    <body>
      <section eId="{xml_escape(eid, {'"': "&quot;"})}">
        <num>{xml_escape(strip_invalid_xml_chars(section.citation))}</num>
        <heading>{xml_escape(strip_invalid_xml_chars(section.heading or f"Section {number}"))}</heading>
        <content>
            {paras_xml}
        </content>
      </section>
    </body>
  </act>
</akomaNtoso>
"""


def _safe_eid(number: str) -> str:
    """Turn a section number like ``"1-339.1"`` into a valid AKN eId.

    eIds must match ``[A-Za-z_][A-Za-z0-9_]*`` — no dots, no dashes.
    """
    out = ["sec_"]
    for ch in number:
        out.append(ch if ch.isalnum() else "_")
    return "".join(out)
