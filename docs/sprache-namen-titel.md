# Open question: Sprache for names, titles and institution names

The prompt fills Sprache only when the description transcribes text with words of a recognisable language, «nicht bei Zahlen, Namen, Orten oder einem einzelnen Wort oder Kürzel». It doesn't say whether work titles and multi-word names of institutions or firms count as «Namen». The model decides this differently from run to run, so Sprache is the least stable LLM column.

In the 2026-11-15 run (prompt with the date changes) Sprache fell from 318 to 275 marks compared with the previous run. Of the 61 marks that lost it, 33 had no words on the mark and 10 were regressions (listed below). The 18 marks here depend on how the rule is read.

**To decide:** do work titles and multi-word institution or firm names on the mark count as text in a language?

If yes, add one sentence to the `sprache` paragraph of `SYSTEM` in `scripts/llm_merkmale.py` and test it on these 18 marks plus the 10 regressions before a full run:

```sh
uv run scripts/llm_merkmale.py parsed extracted /tmp/llm --fotos --ids 1312#6 174#1 1855#5 2262#3 1556#7 4744#5 5393#6 6#4 3603#13 6792.1#1 7061#3 6499#2 5211#6 1494#5 1541#2 1541#9 1330#3 4961#1 1528#6 1558#3 1575#5 1713#4 3078#5 4422#4 5000#12 6543#2 6792.1#5 646#6
```

If no, write the rule into `merkmal-schema.md` and the prompt. Either way, the decision then holds across runs.

## The 18 marks

The column «Vorher» shows the Sprache of the previous run. All 18 marks have no Sprache in the current run.

### Work titles (with artist name, date or number)

| Merkmal | Vorher | Beschreibung |
|---|---|---|
| [1312#6](../trace/1312-6.html) | Deutsch | m. Leinwand: «AUGUSTO GIACO-METTI \| 1944 \| „ Die Nachtschwester “», wohl von Künstlerhand |
| [174#1](../trace/174-1.html) | Deutsch | u. m. Zierrahmen: angeschraubte Metallplakette, eingraviert «ALBERT ANKER 1831 – 1910 \| „Grossvater mit Enkel“» |
| [1855#5](../trace/1855-5.html) | Deutsch | o. r. Keilrahmen: handschriftlich mit Bleistift «Kniendes Mädchen 1913» |
| [2262#3](../trace/2262-3.html) | Deutsch | o. r. Keilrahmen: … handschriftlich «5857 \| Alex. Calame \| Vallée bei \| Chamonix» |
| [1556#7](../trace/1556-7.html) | Französisch | u. m. Keilrahmen: … Galerie Dr. Raeber, Basel, handschriftlich … «63380 \| F. Vallotton (1865-1925) \| Paysage à Perouse \| 1913» |
| [4744#5](../trace/4744-5.html) | Französisch | m. l. waagrechte Mittelstrebe Keilrahmen: Etikett Galerie Paul Vallotton, Lausanne, handschriftlich «№ 987 \| F. Vallotton \| Femme blonde nue \| 116 x 81» … |
| [5393#6](../trace/5393-6.html) | Französisch | o. Mittelstrebe Keilrahmen: … Galerie Paul Vallotton, … Lausanne, handschriftlich … «№ 853. \| F. Vallotton \| Baigneuse», daneben «61x50» |
| [6#4](../trace/6-4.html) | Deutsch | o. m. Zierrahmen: angehefteter Zettel «SIGISMUND RIGHINI \| Tisch mit Blumen, 1908 \| Sammlung E. Wolfensberger» … |
| [4961#1](../trace/4961-1.html) | Englisch | o. m. Keilrahmen: … Werktitel: «Der junge Keller mit …», Leihgeber:in: «L. Schoop – Frauenfeld» |

6#4 also has «Sammlung», a German common noun. 4961#1's previous value (Englisch, probably from the photo) doesn't match the German title.

### Names of institutions and firms

| Merkmal | Vorher | Beschreibung |
|---|---|---|
| [3603#13](../trace/3603-13.html) | Deutsch | u. siebte Leiste: … Stempel … «BÜNDNER KUNSTMUSEUM CHUR \| INV.NR.» und handschriftlich … «9364.000.2003 \| GIACOMETTI, AU-GUSTO» |
| [5211#6](../trace/5211-6.html) | Italienisch | o. l. Keilrahmen: schwarz gestempelt «DO-GANA SVIZZERA CASTASE[GNA]»; Schweizer Zollstempel |
| [1494#5](../trace/1494-5.html) | Deutsch | r. m. Spannrahmen: Reste eines … Etiketts, schwarz gedruckt «Kunstgesellschaft […] \| № […]07» |
| [1541#2](../trace/1541-2.html) | Deutsch | o. m. Malkarton: rundes weisses Etikett, rot gedruckt «DAS KUNSTHAUS \| DAS \| KUNST \| HAUS \| MA[NN]HEIM Q7 17a» |
| [1541#9](../trace/1541-9.html) | Deutsch | m. l. Malkarton: … Etikett …, schwarz gedruckt «Württ. Kunstverein \| Stuttgart \| Kunstgebäude \| 846» |
| [1330#3](../trace/1330-3.html) | Deutsch | u. m. Zierrahmen: weisses Etikett, schwarz und grün bedruckt, «Palette \| Einrahmungen & Vergoldungen» |

### Other

| Merkmal | Vorher | Beschreibung |
|---|---|---|
| [7061#3](../trace/7061-3.html) | Französisch | o. Karton: handschriftlich mit Bleistift «Madame Du Bois \| 10 Chemin Peschier \| Champel \| Genève» (address) |
| [6792.1#1](../trace/6792.1-1.html) | Französisch | o. m. Spannrahmen: handschriftlich … «Al[légr]esse \| [J]ubel» (one word each in French and German) |
| [6499#2](../trace/6499-2.html) | Englisch | o. l. Zierrahmen: … Etikett …, schwarz gedruckt «10 APR 95 \| Sale 1199 \| Lot 45» (auction label keywords) |

## Related: the 10 regressions in the same run

These marks have multi-word text in a recognisable language, but lost Sprache only because of run-to-run variation. A rerun may restore them: 1528#6, 1558#3, 1575#5, 1713#4, 3078#5, 4422#4, 5000#12, 6543#2, 6792.1#5, 646#6.
