# Provenance marks – schema for the Datenblatt parser

Target schema for turning the descriptions of individual provenance marks (`provenienzmerkmale.merkmale[].beschreibung` in `extracted/json/<inv>.json`) into structured data.

Based on `old/schema-notes.md` (25 columns, label register). Everything there still applies unless a revision below changes it.

## Revisions from `old/schema-notes.md`

1. **Technik:** new value **Siegel**, for wax seals (Wachssiegel, Wachsstempel).
2. **Schreibmittel:** «Wachs» is no longer a value, because wax on its own always means a seal (see 1).
   Wax crayons are writing implements: **Wachsstift**. The source also calls them «Wachskreide», which is coded as Wachsstift too.
   «Wachsfarbe» is the colour of the crayon's wax: «mit blauer Wachsfarbe» → Schreibmittel `Wachsstift`, Farbe der Schrift `blau`.
   Where the cataloguer names alternatives, record each implement (see *Several values* below): «Farb- oder Wachsstift» → `Farbstift; Wachsstift`, Bemerkungen `Farb- oder Wachsstift, unsicher`.
3. **Funktion** is replaced by three columns:
   - **Ereignis**: the kind of provenance event the mark records.
   - **Besitz**: the person or institution that owned the work.
   - **Ausstellung**: the exhibition the mark refers to.
4. **Dropped:** Nummer (wie gelesen), Transkription.
5. **Renamed:** Datum (ISO 8601) → **Datum (ISO 8601-2:2019)**. The value can now express uncertainty, approximation, unspecified digits, intervals and sets (see below). This supersedes the earlier decision not to adopt EDTF.
6. **Bemerkungen redefined:** free text for a doubt or an error in the source, e.g. the cataloguer was unsure of the implement, or the description contradicts the photo. It is no longer a place for information from dropped columns or for finer forms (Malerband, Zettel); those stay in Originalbeschreibung. Uncertain dates are expressed in the date value itself.
7. **Vocabulary from the Datenblätter:**
   - Typ: new values
     - **Plaketten**: metal plaques and plates (Metallplakette, Messingschild).
     - **Erwerbsnachweise**: SKKG's own acquisition records attached to the work, when the description starts with «Erwerbsnachweis». A mark that only mentions one («verdeckt von Erwerbsnachweis») keeps its own Typ.
     - **Dokumente**: photocopies, catalogue extracts, newspaper cuttings and other attached papers («weisser Zettel, Kopie», «Auszug aus dem Auktionskatalog»), also when the source calls them Zettel. Not when a catalogue is only cited («gemäss Auktionskatalog»).
   - Typ, other forms: stencilled marks are «Beschriftungen»; scratched-in marks («eingeritzt», «eingekratzt») and monograms are «Inschriften»; coloured dots («roter Punkt») are «Markierungen».
   - Technik: new values **graviert** (also «eingraviert»), **schabloniert** and **eingeritzt** (also «eingekratzt»).
   - Form: new values **quadratisch**, **hochrechteckig**, **achteckig**, **wappenförmig**. Form is the outline of the carrier (label, stamp, seal, slip); shapes of drawn symbols or perforation patterns («sternförmige Markierung», «kreuzförmig perforiert») are not Form. Each outline is its own value: «quadratisch» is kept separate from «viereckig» (any four-cornered shape); the two are not ordered as broader and narrower.
   - Schreibmittel: new values **Tinte**, **Kugelschreiber**, **Pinsel**, **Filzstift**, **Fettstift**, **Stift** (unspecified). Paint («mit schwarzer Farbe») is not a Schreibmittel.
   - Rand / Ecken: «gezahnt» is coded as **gezackter Rand**.
   - Art des Urhebers: new values **Künstler:in** (signatures and inscriptions by the artist), **Forschungsinstitut** (SIK-ISEA control-number labels), **Transportunternehmen** (art shippers such as MAT Securitas Express). Added after the LLM spot check: **Rahmenmacher:in** (framers, gilders, framing shops), **Restaurator:in** (conservation notes such as «doubl. ret.»), **Gutachter:in** (experts, certificates of authenticity), **Institution / Stiftung** (catch-all for cultural foundations such as Pro Helvetia, companies and archives with no value of their own).
   - Not recorded: «verbräunt» and «vergilbt» describe condition, not colour, so they go in no colour column. Later changes to a mark (unterstrichen, durchgestrichen, eingekreist) are not recorded, and their colour is not Farbe der Schrift.

## Cross-references

A mark described only as «siehe vorausgehendes Provenienzmerkmal» takes over the physical columns (Typ to Schreibmittel) of the previous mark on the same sheet; Anbringungsort stays its own. Bemerkungen: `Physische Angaben aus <Merkmal-ID> übernommen (Verweis)`.

## Several values

Every column that can hold more than one value joins them with «; », whether the mark shows all of them or the cataloguer was unsure which one applies. Doubt is never written into the value: no «oder», «?» or «wohl». It goes in Bemerkungen as a brief note that names the column.

| Originalbeschreibung (excerpt) | Value | Bemerkungen |
|---|---|---|
| bedruckt in Schwarz und handschriftlich ergänzt in Rot | Technik `gedruckt; handschriftlich` | |
| gedruckt oder gestempelt | Technik `gedruckt; gestempelt` | `Technik: gedruckt oder gestempelt, unsicher` |
| gestempelt in Schwarz oder Blau | Farbe der Schrift `schwarz; blau` | `Farbe der Schrift: schwarz oder blau, unsicher` |
| mit blauem Farb- oder Wachsstift | Schreibmittel `Farbstift; Wachsstift` | `Schreibmittel: Farb- oder Wachsstift, unsicher` |
| Siegel oder Stempelabdruck | Technik `Siegel; gestempelt` | `Technik: Siegel oder Stempelabdruck, unsicher` |

Several notes in one row are joined with «; ».

Exceptions: «(erschlossen)» in Art des Urhebers and Ereignis marks an inference made while coding, not a doubt in the source, and stays in the value. Dates use the ISO 8601-2 qualifiers (`?`, `~`, `[a,b]`), because that standard was chosen for them.

## Columns (25)

| Spalte | Werte / Inhalt |
|---|---|
| Merkmal-ID | `<Inventarnummer>#<nr>`, e.g. `5758.2#1` (same form `validate.py` prints) |
| Quellzeile | `nr` of the mark in its Datenblatt |
| Inventarnummer | from the PDF filename, as in `extracted/` |
| Foto | all of the mark's `bilder[].datei`, relative to `extracted/`, joined with «; », e.g. `images/1007/1007_007_provenienzmerkmale.jpeg` |
| Originalbeschreibung | `beschreibung`, unchanged |
| Typ | Etiketten · Stempel · Beschriftungen · Markierungen · Inschriften · **Plaketten** · **Erwerbsnachweise** · **Dokumente** · Reste entfernter Provenienzmerkmale. Seals are «Stempel». |
| Technik | gedruckt · handschriftlich · gestempelt · maschinenschriftlich · **Siegel** · **graviert** · **schabloniert** · **eingeritzt** |
| Form | viereckig · quadratisch · rechteckig · langrechteckig · hochrechteckig · achteckig · rund · oval · wappenförmig |
| Rand / Ecken | gezackter Rand (also «gezahnt») · perforierter Rand · abgerundete Ecken |
| Hintergrundfarbe | colour of label, tape or slip |
| Farbe der Umrandung | |
| Farbe der Schrift | colour of ink, print, stamp or seal wax |
| Schreibmittel | Bleistift · Kreide · Farbstift · **Wachsstift** · **Tinte** · **Kugelschreiber** · **Pinsel** · **Filzstift** · **Fettstift** · **Stift**; only where the source names it |
| Anbringungsort | Leinwand · Keilrahmen · Kupferplatte · Holztafel · Zierrahmen · Malkarton; also recto, angeheftet, Mitte links |
| Art des Urhebers | Kunsthandel / Galerie · Auktionshaus · Museum / Ausstellungsort · Zoll · Denkmalbehörde · Privatbesitz / Nachlass · Künstlerbedarf · **Künstler:in** · **Forschungsinstitut** · **Transportunternehmen** · **Rahmenmacher:in** · **Restaurator:in** · **Gutachter:in** · **Institution / Stiftung**; «(erschlossen)» = inferred |
| Urheber (normalisiert) | who issued or applied the mark |
| Ort | German place names |
| Land | |
| **Ereignis** | see below |
| **Besitz** | see below |
| **Ausstellung** | see below |
| Datum (wie gelesen) | when the mark was made or the event it records took place; not dates of the work, including the year in a signature |
| **Datum (ISO 8601-2:2019)** | see below |
| Sprache | |
| **Bemerkungen** | free text: doubts or errors only (revision 6, *Several values*) |

### Ereignis

Auktion · Verkauf · Vermietung · Leihgabe · Inventarisierung · Zollabfertigung · Ausfuhr

- Several values are combined with «; », e.g. `Leihgabe; Verkauf`.
- «(erschlossen)» = inferred, as in Art des Urhebers, e.g. `Auktion (erschlossen)` from a bare «Lot 191».
- Empty when the mark records no event, e.g. a collector's stamp or a supplier's stamp.
- An exhibition label is «Leihgabe» only when it names a lender or owner; otherwise Ereignis stays empty and the exhibition goes in Ausstellung.

### Besitz

The owner the mark names or implies at the time of the mark, as a normalised name, e.g. `Eugen Spitzweg`, `Nachlass Spitzweg`, `Galerie Dr. Raeber`.

- Besitz is not the same as Urheber. An auction house or a customs office issues a mark but does not own the work. A dealer's stock label makes the dealer both Urheber and Besitz.
- A lender named on an exhibition label is Besitz («Leihgeber Herrn K.A. Haegler» → `K.A. Haegler`).
- Erwerbsnachweise: Bruno Stefanini is Urheber or Besitz only where the mark or the Datenblatt comment names him («verfasst von Bruno Stefanini»); there is no default.
- Keep SKKG's anonymisation («Privatperson», «Unbekannte:r Eigentümer:in»). Do not resolve it.

### Ausstellung

The exhibition as the mark names it, with venue and year where given, e.g. `Ausstellung der Sammlung Gerhard Saner, Villa Malpensata, Lugano, 1975`. Empty if the mark does not refer to an exhibition.

### Datum (ISO 8601-2:2019)

ISO 8601-1 dates plus the ISO 8601-2 extensions. EDTF is a profile of ISO 8601-2, so EDTF parsers can read these values.

| Case | Value |
|---|---|
| exact | `1996-12-10`, `1938-12`, `1865` |
| uncertain | `1944?` |
| approximate | `1900~` |
| uncertain and approximate | `1900%` |
| unspecified digits | `19XX`, `1996-XX` |
| interval | `1918-05/1918-06`, `1944/1950` |
| open interval | `1944/..`, `../1944` |
| one of several | `[1996-12-10,1996-10-12]` (day/month order unclear) |

## Migrating old register rows

| Funktion (old) | Ereignis | Besitz | Ausstellung |
|---|---|---|---|
| Auktion | Auktion | | |
| Auktion (wahrscheinlich) | Auktion (erschlossen) | | |
| Auktion / Nachlassversteigerung | Auktion | the estate, e.g. `Nachlass Calame` | |
| Kunsthandel (Bestand / Verkauf) | Verkauf | the dealer | |
| Kunsthandel (Verkauf / Vermietung) | Verkauf; Vermietung | the dealer | |
| Inventar / Bestand | Inventarisierung | the holder | |
| Besitz / Sammlung | | the collector | |
| Nachlass | | the estate | |
| Ausstellung | | | the exhibition |
| Ausstellung / Leihgabe | Leihgabe | the lender | the exhibition |
| Zoll | Zollabfertigung | | |
| Ausfuhr / Denkmalschutz | Ausfuhr | | |
| Materiallieferant | | | |

Materiallieferant marks keep their meaning through Art des Urhebers «Künstlerbedarf»; they record neither an event nor an owner.
Old rows with Schreibmittel «Wachs» (M023, M043) become Technik «Siegel» with an empty Schreibmittel.
Old values with « oder » (M002 Farbe der Schrift `schwarz oder blau`, Technik `gedruckt oder gestempelt`) become «; » lists with a note, as in *Several values*.
Old Bemerkungen carry over only where they record a doubt or error (e.g. M004 «Reihenfolge Tag/Monat unklar»); the rest is dropped.
