# glamhack26-prov-parser

Extracts structured data from the provenance research data sheets (Provenienz-Datenblätter) of the Stiftung für Kunst, Kultur und Geschichte (SKKG), Winterthur, and parses the provenance marks (labels, stamps, inscriptions) they describe.

## Pipeline

1. `scripts/extract_datenblatt.py` reads the PDF data sheets and writes JSON, CSV, Excel and images to `extracted/`.
2. `scripts/parse_merkmale.py` fills the physical columns of each provenance mark with rules and writes `parsed/`.
3. `scripts/llm_merkmale.py` fills the interpretive columns (issuer, place, event, owner, dates, language) with Claude and writes `parsed/llm/`. Every value has a trace that shows its source and evidence.

The column vocabulary is defined in `merkmal-schema.md`.

## Usage

The scripts declare their dependencies inline, so they run with [uv](https://docs.astral.sh/uv/) and no separate setup. The source PDFs are not part of this repository.

```sh
uv run scripts/extract_datenblatt.py Provenienz_Datenblatt extracted
uv run scripts/validate.py Provenienz_Datenblatt extracted
uv run scripts/parse_merkmale.py extracted parsed
uv run scripts/llm_merkmale.py parsed extracted parsed/llm --fotos   # needs ANTHROPIC_API_KEY
```

## License

The code is released under the MIT License (see `LICENSE`). The source data belongs to the SKKG.
