# Output format

Every scraper emits normalized source-section text plus deterministic sidecar
metadata as a local ingest intermediate. Generated scrape output should not be
committed to Git or uploaded to R2.

## File layout

```
out_root/
└── {jurisdiction}/                 # us-il, us-ny, us-federal
    └── {doc_type_dir}/             # statutes, regulations, guidance, manual
        └── {optional chapter dir}/
            ├── {section_number}.txt
            └── {section_number}.meta.yaml
```

Scrapers are free to add a chapter, title, or year directory level when the
source has one. Example from Illinois:

```
out/us-il/statutes/
├── ch-1/
│   ├── 1-1-1.txt
│   ├── 1-1-1.meta.yaml
│   ├── 1-1-2.txt
│   └── 1-1-2.meta.yaml
├── ch-35/
│   ├── 35-155-1.txt
│   ├── 35-155-1.meta.yaml
│   ├── 35-155-2.txt
│   └── 35-155-2.meta.yaml
└── ...
```

## Text file

The `.txt` file contains only normalized source body text. Paragraphs are
separated by one blank line and the file ends with a trailing newline when it
has content.

```text
As used in this Act:

"Renting" means any transfer of the possession or use of property.
```

Headings and citations live in metadata, not in the text body.

## Metadata file

The `.meta.yaml` sidecar has one record with deterministic key order:

```yaml
format: axiom-source-section/v1
jurisdiction: "us-il"
doc_type: "statute"
authority_code: "ILCS"
work_number: "35-155-2"
citation: "35 ILCS 155/2"
heading: "Definitions"
generation_date: "2026-04-20"
source:
  author_id: "il-legislature"
  author_name: "Illinois General Assembly"
  author_url: "https://www.ilga.gov"
text_path: "35-155-2.txt"
```

## Axiom ingest contract

The Axiom corpus ingester reads:

* `work_number` -> the source section identifier used to build citation paths.
* `citation` -> rendered citation text.
* `heading` -> section heading.
* `{text_path}` -> body text, with paragraphs already normalized.
* `jurisdiction`, `doc_type`, and `authority_code` -> corpus metadata.

Changes that affect these fields require a coordinated Axiom corpus update.

## Section-number rules

* Use the state's canonical short-cite form as `work_number`. It must be unique
  within the jurisdiction and document type.
* Preserve dots, dashes, colons, and alpha suffixes when they are meaningful in
  the source citation.
* Replace slashes with underscores in filenames. The original citation text can
  still appear in `citation`.
