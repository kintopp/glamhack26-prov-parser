# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Extracts structured data and images from SKKG (Stiftung für Kunst, Kultur und Geschichte) provenance research data sheets: 236 PDFs in `Provenienz_Datenblatt/` named `Datenblatt_Provenienzforschung_<Inventarnummer>_<YYMMDD>.pdf`. Output goes to `extracted/` (generated; safe to delete and rebuild).

`old/` is earlier work on a different source, the SKKG label register spreadsheet; it is not used by the current pipeline.

## References

- SKKG Linked Data usage guide (skkg-pipeline wiki): https://github.com/swiss-art-research-net/skkg-pipeline/wiki/6.-How-to-use-the-SKKG-Linked-Data

## Commands

Both scripts carry PEP 723 dependency headers, so `uv run` needs no venv.

```sh
# full run (~30 s): JSON, CSV, Excel, images
rm -rf extracted && uv run scripts/extract_datenblatt.py Provenienz_Datenblatt extracted

# single document while iterating (SRC may be one PDF)
uv run scripts/extract_datenblatt.py Provenienz_Datenblatt/Datenblatt_Provenienzforschung_1007_221107.pdf /tmp/out

# regression check against the PDFs (run after every change)
uv run scripts/validate.py Provenienz_Datenblatt extracted
```

There are no unit tests; `validate.py` is the test. It checks object fields, footnote numbering and references, that every image is placed, and that every body word in the PDF appears in the JSON. It needs a full run: it reads `OUT/json/<inv>.json` for every PDF in SRC, so it crashes on a single-document OUT. Expected output on a clean run is exactly three source-data findings: `3542-1 vs 3542.1` (filename uses "-", sheet uses "."), `3542-2 vs 3541.2` (typo in the PDF), and mark `5758.2#1` has no photo. Anything else is a regression.

Provenance-mark parser (schema: `merkmal-schema.md`), rule layer only so far; reads `extracted/json/`:

```sh
uv run scripts/parse_merkmale.py extracted parsed
```

It writes `parsed/merkmale.json` (a `lauf` block with parser/schema hashes, then records with a per-value `trace`: method, rule id and character span in Originalbeschreibung, for the planned HTML trace pages), `parsed/merkmale.csv` and `parsed/unbekannt.tsv` (words outside «…» that no rule explained, grouped by kind). Rules fill the physical columns; the interpretive columns are left for the LLM layer, which takes precedence there (the reverse of the Rijksmuseum parser). There is no automated check yet: after a rule change, compare the column fill rates the script prints and spot-check records.

LLM layer (needs `ANTHROPIC_API_KEY`, read from the environment or `~/.env`; never print it):

```sh
uv run scripts/llm_merkmale.py parsed extracted parsed/llm            # all candidates, Sonnet 5.5 (~$9)
uv run scripts/llm_merkmale.py parsed extracted /tmp/llm --sample 55  # stratified test sample
uv run scripts/llm_merkmale.py parsed extracted /tmp/llm --ids 1713#3 4961#2
```

It writes `merkmale.json`/`.csv` (rule values plus LLM values, traces with `methode: "llm"`, quote, span and reason), `llm_probleme.tsv` (rejected values, vocabulary proposals, API errors) and `cache/<model>/<id>.json`. The cache key covers prompt, schema, effort and the mark's input, so a rerun after a validation-only change costs nothing.

## Merkmal parser (`scripts/parse_merkmale.py`)

- The user decides all vocabulary. A new value goes into both the parser and `merkmal-schema.md` (revision 7 lists the values that came from the Datenblätter).
- Rules run on `Mark.masked`: «…», […] and (…) are blanked with spaces so offsets stay valid. Transcriptions are for the LLM layer, not for rules.
- Typ is decided by the first clause (up to the first «,», «;» or «»); later clauses often describe neighbouring things («verdeckt durch Etikett»). Erwerbsnachweise and Dokumente are checked before the Typ table.
- Trace `methode` values: `regel` (rule), `verweis` (copied from the previous mark for «siehe vorausgehendes Provenienzmerkmal», with `von`), later `llm`.
- LLM layer (`scripts/llm_merkmale.py`): the model may write only the interpretive columns, Bemerkungen, and Typ where the rules left it empty. `merge()` rejects values outside the vocabularies, invalid ISO 8601-2 dates, quotes not found in description/comment, and Urheber/Besitz names that occur only in the sheet's provenance chain (the chain may normalise a name, not supply it). Sonnet 5.5 is the default; Haiku 5.5 confused lenders with issuers in testing.
- The descriptions sometimes omit printed text that identifies the issuer (1713#3 MAT Securitas Express, 4961#2 «F. HODLER, ARCHIV»). Check doubtful marks against the photo in the `Foto` column.
- Several values are joined with «; »; doubt goes into Bemerkungen as one note per source passage naming every affected column. Values inferred from another word (Technik from a Schreibmittel) are put with `doubt=False`.

For ad-hoc probing, `uv run --with pymupdf python script.py`; plain `uv run python` has no pymupdf.

## Architecture (`scripts/extract_datenblatt.py`)

The sheets are Word exports (PDFMaker, plus 27 via PScript5) with ruled tables and a fixed template of 8 sections: OBJEKT, PROVENIENZ, RECHERCHE, ANFRAGEN, ARCHIV INTERN, ARCHIVE EXTERN, KONTEXTINFORMATIONEN, PROVENIENZMERKMALE. Parsing is geometric, not text-heuristic:

1. **`load_page`** reads characters from `rawdict` (with size, bold, italic, page), images, and vector drawings. Thin grey filled rects are table rules; a black rule at x≈57 (144 pt wide, or full width on continuation pages) is the footnote separator, so `fn_top` bounds the body.
2. **`regions`** splits pages at section headings (fixed order) into `(section, page, y0, y1, continued)` slices.
3. **`page_rows`** builds the cell grid: horizontal rules → row bounds; vertical rules (clustered, 6 pt tolerance) → columns. A column boundary counts in a row only if a rule crosses that row, which is how merged cells are handled. Characters are assigned per char, so a line spanning cells is split.
4. **`section_rows`** concatenates pages and merges a row split by a page break into the previous row (`continues_first_col`, or `continues_mark` for the marks table). `fold()` normalises rows to the section's column count first.
5. **`Doc`** section parsers (`objekt`, `table`, `recherche`, `merkmale`) turn cells into records. `cell_content` is the shared cell→`{text, bilder, fussnoten}` converter.
6. **`main`** writes `datenblaetter.json`, `json/<inv>.json`, `csv/*.csv`, `datenblaetter.xlsx` (one sheet per CSV table) and `images/<inv>/`.

Conventions that span several functions:

- Footnote reference marks (superscript digits, < 8.5 pt) become `⟦n⟧` in `Line.text()` and are stripped by `split_marks`, which returns the refs. Every record then carries `fussnoten: [{nr, text}]`. Refs can sit in label cells too (RECHERCHE category, marks side column).
- Paragraph breaks are vertical gaps > 1.45 × font size. Across a page break the paragraph continues only if the previous line ends in "-" or "," or the next line starts lowercase.
- `join_lines` undoes Word hyphenation: before lowercase the hyphen is dropped, before uppercase it is kept ("Haegler-Passavant"), before "und/oder/bzw./sowie" it is kept with a space. URLs are kept intact.
- Images: a paragraph ending in ":" right before an image is its `bildtitel`; one right below (< 14 pt) is its `bildunterschrift`. Single images are saved byte-for-byte; images sliced into abutting horizontal strips (`Img.tiles`) are stitched into one PNG.
- OBJEKT fields come from type style: bold = artist, italic = title; upright titles fall back to "lines up to the first `DATE_LIKE` line". Each visual line is one field; only a trailing "-", "," or "/" joins lines. Raw lines stay in `zeilen`.
- Rows that contain only "–" are placeholders and are dropped. In the marks table the side is recorded in `ohne_merkmale`, and image-only rows are attached to the previous mark (or to the overview).
- Output keys are German snake_case (`kuenstler`, `besitzer`, `merkmal`), matching the source.
- The inventory number used as the key (`json/<inv>.json`, `images/<inv>/`, the `inventarnummer` column) comes from the filename. `objekt.inventarnummer` is what the sheet prints and may differ.

## Common Errors

- **Symbol-font dash:** Word's Symbol en dash arrives as U+F02D and looks invisible. It is mapped in `SYMBOL_PUA`; add new private-use characters there.
- **`find_tables()` is unreliable here:** it splits cells on the grey header fills and loses spaces. Use the rule-based grid.
- **Merkmal rules starving each other:** `Mark.covered` marks words as explained for the unknown-word report. Don't use it to skip matches in another rule: doing so once dropped Schreibmittel from 290 marks to 4. Check against trace entries of the specific rule instead.
- **Form from a drawn shape:** «mit rundem schwarzem Punkt», «runden … Symbols» are not carrier outlines; the Form loop skips a shape word followed (through lowercase words only) by Punkt/Symbol/Zeichen/Markierung/Kreis.
- **`validate.py` imports the extractor:** keep extractor-only dependencies (`openpyxl`) imported inside the functions that use them, or add them to `validate.py`'s header.
