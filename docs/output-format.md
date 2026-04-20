# Output format

Every scraper emits [Akoma Ntoso 3.0](https://www.oasis-open.org/committees/download.php/59858/akn-core-v1.0-cos01-part1.pdf)
XML files. Atlas's `ingest_state_laws.py` is the primary consumer and
keys on specific elements — this doc pins the contract.

## File layout

```
out_root/
└── {jurisdiction}/                 # us-il, us-ny, us-federal
    └── {doc_type}/                 # statute, regulation, guidance, manual
        └── {optional chapter dir}/
            └── {section_number}.xml
```

Scrapers are free to add a chapter / title directory level when the
source has one — makes the tree browseable. Example from IL:

```
out/us-il/statute/
├── ch-1/
│   ├── 1-1-1.xml
│   └── 1-1-2.xml
├── ch-35/
│   ├── 35-155-1.xml
│   └── 35-155-2.xml
└── ...
```

## XML shape

```xml
<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act name="section">
    <meta>
      <identification source="#axiom">
        <FRBRWork>
          <FRBRthis value="/akn/us-il/act/ilcs/35-155-2"/>
          <FRBRuri value="/akn/us-il/act/ilcs/35-155-2"/>
          <FRBRauthor href="#il-legislature"/>
          <FRBRcountry value="us-il"/>
          <FRBRnumber value="35-155-2"/>
          <FRBRname value="ILCS"/>
        </FRBRWork>
        <FRBRExpression>
          <FRBRthis value="/akn/us-il/act/ilcs/35-155-2/eng@2026-04-20"/>
          <FRBRuri value="/akn/us-il/act/ilcs/35-155-2/eng@2026-04-20"/>
          <FRBRdate date="2026-04-20" name="publication"/>
          <FRBRauthor href="#axiom"/>
          <FRBRlanguage language="eng"/>
        </FRBRExpression>
        <FRBRManifestation>
          <FRBRthis value="/akn/us-il/act/ilcs/35-155-2/eng@2026-04-20/main.xml"/>
          <FRBRuri value="/akn/us-il/act/ilcs/35-155-2/eng@2026-04-20/main.xml"/>
          <FRBRdate date="2026-04-20" name="generation"/>
          <FRBRauthor href="#axiom"/>
        </FRBRManifestation>
      </identification>
      <references source="#axiom">
        <TLCOrganization eId="il-legislature" href="https://www.ilga.gov"
                         showAs="Illinois General Assembly"/>
        <TLCOrganization eId="axiom" href="https://axiom-foundation.org"
                         showAs="Axiom Foundation"/>
      </references>
    </meta>
    <body>
      <section eId="sec_35_155_2">
        <num>35 ILCS 155/2</num>
        <heading>Definitions</heading>
        <content>
            <p>As used in this Act:</p>
            <p>"Renting" means any transfer of the possession…</p>
        </content>
      </section>
    </body>
  </act>
</akomaNtoso>
```

## Atlas ingest contract

Atlas's `ingest_state_laws.py` reads:

* `<FRBRWork>/<FRBRnumber value="…">` → section identifier; forms the
  Atlas `citation_path`.
* `<body>//<section>/<num>` → rendered citation text.
* `<body>//<section>/<heading>` → Atlas `heading` column.
* `<body>//<section>/<content>/<p>…</p>` → joined on blank lines to
  form Atlas `body` column.

Changes that affect these four readers require coordinated PRs in
atlas.

## FRBRdate deliberately omits `name="enacted"`

We don't know the real enactment date of each scraped section; writing
the scrape date there would mislead downstream consumers. We emit only
`publication` and `generation` (both the scrape date) at the Expression
and Manifestation levels. If a scraper starts parsing a real enactment
date from source metadata, it should add a separate `<FRBRdate
name="enacted">` at the Work level.

## Section-number rules

* Use the state's canonical short-cite form as `FRBRnumber`. Unique
  within the jurisdiction.
* Preserve dots, dashes, colons (DC UCC `28:9-316`, IL `35-155-2.1`).
  The `<section eId="…">` attribute converts non-alphanumerics to
  underscores — that's AKN's rule, not ours.
* When a section has alpha suffixes (`101A`, `204.1a`), preserve them;
  they're meaningful.
