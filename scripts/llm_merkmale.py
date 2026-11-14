# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic>=0.75"]
# ///
"""LLM layer of the provenance-mark parser (schema: merkmal-schema.md).

Usage: uv run scripts/llm_merkmale.py PARSED_DIR EXTRACTED_DIR OUT_DIR
           [--model claude-sonnet-5-5] [--effort medium] [--sample N | --ids ID ...] [--workers 8]

Reads PARSED_DIR/merkmale.json (rule layer) and fills the interpretive columns. Rule values
are fixed: the model sees them but can only write the LLM columns, Bemerkungen, and Typ where
the rules left it empty. Vocabulary columns are enums in the output schema, and each date comes
as one {wie_gelesen, iso} pair. Every value and every Bemerkung must quote its evidence; quotes
that occur nowhere in the source, malformed dates and names found only in the provenance chain
are rejected and listed in OUT_DIR/llm_probleme.tsv instead of being written.

Responses are cached per mark in OUT_DIR/cache/<model>/, keyed by prompt hash, so reruns only
call the API for marks whose input or prompt changed. The key is read from ANTHROPIC_API_KEY,
falling back to ~/.env.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
import json
import os
import random
import re
import sys
import unicodedata
from pathlib import Path

import anthropic

from parse_merkmale import COPIED, LLM_COLUMNS, sha, write_csv

ART = ["Kunsthandel / Galerie", "Auktionshaus", "Museum / Ausstellungsort", "Zoll", "Denkmalbehörde",
       "Privatbesitz / Nachlass", "Künstlerbedarf", "Künstler:in", "Forschungsinstitut", "Transportunternehmen"]
EREIGNIS = ["Auktion", "Verkauf", "Vermietung", "Leihgabe", "Inventarisierung", "Zollabfertigung", "Ausfuhr"]
TYP = ["Etiketten", "Stempel", "Beschriftungen", "Markierungen", "Inschriften", "Plaketten", "Erwerbsnachweise",
       "Dokumente", "Reste entfernter Provenienzmerkmale"]
SPRACHE = ["Deutsch", "Französisch", "Italienisch", "Englisch", "Niederländisch", "Spanisch", "Latein",
           "Rätoromanisch", "Schwedisch", "Dänisch", "Russisch", "Polnisch", "Tschechisch", "Ungarisch"]
# output field -> column; "typ" only applies where the rules left Typ empty; dates come as pairs ("daten")
FELDER = dict(zip(["art_des_urhebers", "urheber", "ort", "land", "ereignis", "besitz", "ausstellung",
                   "datum_wie_gelesen", "datum_iso", "sprache", "typ"], LLM_COLUMNS + ["Typ"], strict=True))
VOCAB = {"art_des_urhebers": ART, "ereignis": EREIGNIS, "sprache": SPRACHE, "typ": TYP}
FREI = ["urheber", "ort", "land", "besitz", "ausstellung"]  # in this order: Land is checked against Ort
ERSCHLIESSBAR = {"art_des_urhebers", "ereignis"}  # «(erschlossen)» is part of the value here
PHYSICAL = COPIED + ["Anbringungsort"]
GLOSSE = {"Künstler:in": "Signaturen und Inschriften von Künstlerhand", "Forschungsinstitut": "z. B. SIK-ISEA",
          "Transportunternehmen": "Kunstspediteure"}

_D = r"(?:\d{4}|\d{3}X|\d{2}XX|XXXX)(?:-(?:\d{2}|XX)(?:-(?:\d{2}|XX))?)?[?~%]?"
ISO_DATE = re.compile(rf"^(?:{_D}|(?:{_D}|\.\.)/(?:{_D}|\.\.)|\[{_D}(?:\.\.{_D})?(?:,{_D}(?:\.\.{_D})?)*\])$")


def liste(vals: list[str]) -> str:
    return " · ".join(f"{v} ({GLOSSE[v]})" if v in GLOSSE else v for v in vals)


SYSTEM = f"""Du codierst Provenienzmerkmale (Etiketten, Stempel, Beschriftungen usw. auf der Rückseite oder dem Rahmen von Kunstwerken) aus den Provenienz-Datenblättern der Stiftung für Kunst, Kultur und Geschichte (SKKG), Winterthur. Gründer und Sammler der SKKG war Bruno Stefanini.

Physische Spalten (Typ, Technik, Form, Farben, Schreibmittel, Anbringungsort) sind bereits durch Regeln gefüllt und stehen fest. Du füllst die interpretierenden Spalten. Jeder Wert braucht einen Beleg.

## Eingabe

Ein JSON-Objekt pro Merkmal:
- `beschreibung`: Beschreibung des Merkmals durch die Bearbeiterin. Text in «…» ist eine Transkription dessen, was auf dem Merkmal steht; «|» = Zeilenumbruch; […] = unleserlich; [?] und «wohl» = unsichere Lesung.
- `kommentar`: Deutung des Merkmals durch die Bearbeiterin, falls vorhanden. Sie ist die verlässlichste Quelle für die Deutung.
- `regelwerte`: die festen physischen Werte.
- `objekt`: Künstler, Titel und Datierung des Werks.
- `provenienzkette`: die im Datenblatt rekonstruierte Besitzgeschichte des Werks. Nur zum Normalisieren von Namen und Orten verwenden, die das Merkmal oder der Kommentar nennen («A. Wey» → «Alfons Wey-Leuthard»), und um den Ort eines genannten Auktionshauses oder Händlers zu finden. Keine Personen oder Ereignisse aus der Kette übernehmen, die Merkmal und Kommentar nicht nennen: Der Eigentümer der Kette zur Zeit des Merkmals ist kein Beleg.
- `vorausgehendes_merkmal`: nur wenn Beschreibung oder Kommentar darauf verweisen.

## Ausgabe

Für `art_des_urhebers`, `ereignis`, `sprache` und `typ` je eine Liste; `urheber`, `ort`, `land`, `besitz` und `ausstellung` gemeinsam in `werte`, mit der Spalte in `feld`. Ein Eintrag pro Wert, mehrere Werte = mehrere Einträge. Leere Spalten bekommen keinen Eintrag. Lieber nichts eintragen als raten.

Jeder Eintrag hat:
- `wert`: der Wert, ohne Zweifelswörter («wohl», «?», «oder»).
- `zitat`: wörtlicher Ausschnitt aus `beschreibung` oder `kommentar` (bei `ort` und `land` auch aus `provenienzkette`), der den Wert belegt. Exakt kopieren, keine Auslassungen, höchstens etwa 120 Zeichen.
- `quelle`: woher das Zitat stammt (`provenienzkette` nur bei `ort` und `land`).
- `begruendung`: ein kurzer deutscher Satz, warum der Wert so codiert ist.
- nur bei `art_des_urhebers` und `ereignis`: `erschlossen` = true, wenn der Wert nicht ausdrücklich dasteht, sondern erschlossen ist (z. B. Auktion aus einer blossen Losnummer).

## Spalten

- `art_des_urhebers`: Art dessen, der das Merkmal ausgestellt oder angebracht hat: {liste(ART)}.
- `urheber`: wer das Merkmal ausgestellt oder angebracht hat, normalisiert («Galerie Dr. Raeber», «Kunsthaus Zürich», «Zollamt Zürich», «Bruno Stefanini»). Personen mit Vor- und Nachname, soweit belegt. Bei Signaturen, Monogrammen und Initialen ist es der Künstler aus `objekt`.
- `ort`: Ort des Urhebers oder des Ereignisses, wie in Beschreibung, Kommentar oder (für ein genanntes Auktionshaus oder einen genannten Händler) Provenienzkette angegeben; deutscher Name (Genf, nicht Genève; Mailand, nicht Milano), ohne Strasse.
- `land`: Land zu `ort` in heutiger deutscher Bezeichnung (Schweiz, Deutschland, Frankreich …), nur wenn der Ort es eindeutig macht oder es dasteht.
- `ereignis`: Art des Provenienzereignisses, das das Merkmal festhält: {liste(EREIGNIS)}. Leer, wenn das Merkmal kein Ereignis festhält (Sammlerstempel, Lieferantenstempel, Signatur). Ein Ausstellungs- oder Transportetikett ist nur dann «Leihgabe», wenn es einen Leihgeber oder Besitzer nennt; sonst bleibt `ereignis` leer und die Ausstellung steht in `ausstellung`.
- `besitz`: Eigentümer:in des Werks zur Zeit des Merkmals, normalisiert. Nicht dasselbe wie Urheber: Auktionshaus und Zoll stellen Merkmale aus, besitzen das Werk aber nicht. Das Lageretikett eines Händlers macht den Händler zu Urheber und Besitz. Ein Leihgeber auf einem Ausstellungsetikett ist Besitz. Anonymisierungen der SKKG («Privatperson», «Unbekannte:r Eigentümer:in») unverändert übernehmen, nie auflösen.
- `ausstellung`: die Ausstellung, so wie das Merkmal sie nennt, mit Ort und Jahr, falls angegeben.
- `daten`: Daten, an denen das Merkmal angebracht wurde oder das Ereignis stattfand, das es festhält; je Datum ein Eintrag mit `wie_gelesen` (genau wie auf dem Merkmal oder in Beschreibung bzw. Kommentar) und `iso` (dasselbe Datum nach ISO 8601-2:2019). Keine Datierungen des Werks, auch nicht die Jahreszahl einer Signatur oder eine Werkdatierung auf einem Ausstellungsetikett. ISO-Formen: `1996-12-10`, `1938-12`, `1865`; unsicher `1944?`; ungefähr `1900~`; beides `1900%`; unbestimmte Stellen `19XX`, `1996-XX`, `XXXX-09-16`; Intervall `1918-05/1918-06`; offen `1944/..`, `../1944`; eins von mehreren `[1996-12-10,1996-10-12]`. Ein Zeitraum «von … bis …» ist ein Datum (ein Intervall), nicht zwei. Zweistellige Jahre nur auflösen, wenn der Kontext das Jahrhundert klärt, sonst mit `XX` für die Jahrhundertstellen.
- `sprache`: Sprache des Texts auf dem Merkmal: {liste(SPRACHE)}. Nur wenn das Merkmal Text in einer erkennbaren Sprache trägt; nicht bei Zahlen, Namen, Orten oder einzelnen Kürzeln wie «Lot».
- `typ`: nur ausfüllen, wenn `regelwerte.Typ` leer ist: {liste(TYP)}. Signaturen und Monogramme sind «Inschriften».

Erwerbsnachweise sind Zettel, mit denen die Erwerbungen der SKKG bzw. Bruno Stefaninis festgehalten wurden: Ereignis ist die Erwerbung (z. B. Auktion). Bruno Stefanini ist nur dann Urheber oder Besitz, wenn Merkmal oder Kommentar ihn nennen («verfasst von Bruno Stefanini»); sonst diese Spalten leer lassen.

## `bemerkungen`

Zweifel oder Fehler in der Quelle, kurz und deutsch, mit den betroffenen Spalten in `felder` und der zweifelhaften Stelle wörtlich in `zitat`: unsichere Lesung eines Namens oder Datums («[?]», «wohl»), Alternativen der Bearbeiterin, Widersprüche zwischen Beschreibung und Kommentar. Nur für Zweifel und Fehler, die in der Quelle stehen. Keine Bemerkung, wenn eine Spalte nur leer bleibt, wenn ein Vokabularwert fehlt (dafür gibt es `vokabular_vorschlaege`), um eigene Codierentscheide zu erklären (dafür gibt es `begruendung`), ein Urheber nicht bekannt ist oder sich eine Zahl keinem Ereignis zuordnen lässt: Fehlendes ist kein Zweifel. Im Zweifel keine Bemerkung.

## `vokabular_vorschlaege`

Wenn ein Merkmal eindeutig einen Wert für `art_des_urhebers`, `ereignis` oder `typ` hätte, der im Vokabular fehlt, hier den passenden Begriff mit Begründung vorschlagen, statt einen falschen Vokabularwert zu wählen."""


def obj(props: dict) -> dict:
    """Closed JSON-schema object with every property required."""
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def arr(item: dict) -> dict:
    return {"type": "array", "items": item}


def enum(vals: list[str]) -> dict:
    return {"type": "string", "enum": vals}


STR = {"type": "string"}
QUELLE = enum(["beschreibung", "kommentar"])
BELEG = {"zitat": STR, "quelle": QUELLE, "begruendung": STR}


def eintrag(f: str) -> dict:
    extra = {"erschlossen": {"type": "boolean"}} if f in ERSCHLIESSBAR else {}
    return arr(obj({"wert": enum(VOCAB[f])} | extra | BELEG))


# one list for the free-text columns: a list per column makes the compiled grammar too large
SCHEMA = obj(
    {f: eintrag(f) for f in VOCAB}
    | {"werte": arr(obj({"feld": enum(FREI), "wert": STR} | BELEG
                        | {"quelle": enum(["beschreibung", "kommentar", "provenienzkette"])})),
       "daten": arr(obj({"wie_gelesen": STR, "iso": STR} | BELEG)),
       "bemerkungen": arr(obj({"felder": arr(enum(list(FELDER))), "text": STR, "zitat": STR, "quelle": QUELLE})),
       "vokabular_vorschlaege": arr(obj({"feld": enum(["art_des_urhebers", "ereignis", "typ"]), "wert": STR,
                                        "begruendung": STR}))})


def load_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    env = Path.home() / ".env"
    for line in env.read_text().splitlines() if env.exists() else []:
        m = re.match(r"\s*(?:export\s+)?ANTHROPIC_API_KEY\s*=\s*(.*)$", line)
        if m:
            os.environ["ANTHROPIC_API_KEY"] = m.group(1).strip().strip("\"'")
            return
    sys.exit("ANTHROPIC_API_KEY not set and not found in ~/.env")


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def kette(doc: dict) -> list[str]:
    return [norm_ws(f"{p.get('zeitraum') or ''}: {p.get('besitzer') or ''}") for p in doc.get("provenienz") or []]


def eingabe(rec: dict, prev: dict | None, doc: dict) -> dict:
    ds, kx = rec["datensatz"], rec["kontext"]
    obj = doc.get("objekt") or {}
    inp = {
        "merkmal_id": ds["Merkmal-ID"],
        "seite": kx["seite"],
        "beschreibung": ds["Originalbeschreibung"],
        "kommentar": kx["kommentar"],
        "regelwerte": {k: ds[k] for k in PHYSICAL},
        "objekt": {"kuenstler": obj.get("kuenstler"), "titel": obj.get("titel"), "datierung": obj.get("datierung")},
        "provenienzkette": kette(doc),
    }
    text = f"{ds['Originalbeschreibung']} {kx['kommentar'] or ''}"
    if prev and re.search(r"vorausgehend", text):
        inp["vorausgehendes_merkmal"] = {"merkmal_id": prev["datensatz"]["Merkmal-ID"],
                                         "beschreibung": prev["datensatz"]["Originalbeschreibung"],
                                         "kommentar": prev["kontext"]["kommentar"]}
    return inp


def call(client, model: str, effort: str, inp: dict) -> dict:
    resp = client.messages.create(
        model=model,
        max_tokens=16000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": json.dumps(inp, ensure_ascii=False, indent=1)}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"stop_reason {resp.stop_reason}")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError("no text block")
    u = resp.usage
    return {"antwort": json.loads(text), "request_id": resp._request_id,
            "usage": {"input": u.input_tokens, "output": u.output_tokens,
                      "cache_read": u.cache_read_input_tokens or 0, "cache_write": u.cache_creation_input_tokens or 0}}


ALIAS = {"skkg": "stiftung für kunst, kultur und geschichte"}


def fold(s: str) -> str:
    """Lower-case without accents, so «BIELER» matches «Biéler»."""
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if not unicodedata.combining(c))


def plain(s: str) -> str:
    """Folded text without transcription brackets, with known abbreviations expanded."""
    s = re.sub(r"[\[\]«»|]", "", s.lower())
    for k, v in ALIAS.items():
        s = re.sub(rf"\b{k}\b", f"{k} {v}", s)
    return fold(s)


# words that do not identify anyone: «Galerie X» must match on X
GENERIC = {"galerie", "galleria", "gallery", "auktion", "sammlung", "nachlass", "familie", "zollamt", "zoll",
           "museum", "kunsthaus", "herr", "frau", "prof", "und", "von", "der", "die", "des"}


def named_in(wert: str, text: str) -> bool:
    """Some word of the value (≥ 3 letters, not a generic word) occurs in the text."""
    words = [w for w in re.findall(r"[^\W\d_]{3,}", fold(wert)) if w not in GENERIC]
    return any(w in text for w in words) if words else True


def find_span(hay: str, quote: str) -> tuple[int, int] | None:
    quote, hay = quote.replace("\xad", ""), hay.replace("\xad", "")
    quote = quote.strip().strip("«»").strip()
    if not quote:
        return None
    i = hay.find(quote)
    if i >= 0:
        return (i, i + len(quote))
    # tolerate whitespace and «|» differences
    pat = r"\s*\|?\s*".join(re.escape(t) for t in quote.replace("|", " ").split())
    m = re.search(pat, hay) if pat else None
    return m.span() if m else None


def merge(rec: dict, inp: dict, out: dict, model: str, probleme: list):
    """Validate the model's answer and write accepted values + traces into rec (in place)."""
    ds, mid, ant = rec["datensatz"], rec["datensatz"]["Merkmal-ID"], out["antwort"]
    quellen = {"beschreibung": ds["Originalbeschreibung"], "kommentar": inp["kommentar"] or ""}
    mit_kette = quellen | {"provenienzkette": "\n".join(inp["provenienzkette"])}
    mark_text = plain(" ".join(quellen.values()) + " " + (inp.get("vorausgehendes_merkmal") or {}).get("beschreibung", ""))
    # the work's artist may be named only in objekt: «Signatur des Künstlers»
    signatur = re.search(r"signatur|monogramm|initialen|künstlerhand|hand de[rs] künstler",
                         " ".join(quellen.values()), re.I)
    artist = plain(re.sub(r"\s*\(.*?\)", "", inp["objekt"].get("kuenstler") or "")) if signatur else ""

    def belegt(w: dict, hays: dict) -> tuple[str, tuple[int, int] | None]:
        """Source and span of the quote; the named source is searched first."""
        return next(((q, sp) for q in dict.fromkeys([w["quelle"], *hays])
                     if (sp := find_span(hays[q], w["zitat"])) is not None), (w["quelle"], None))

    def trace(col: str, wert: str, w: dict, quelle: str, span):
        rec["trace"].append({"feld": col, "wert": wert, "methode": "llm", "modell": model, "quelle": quelle,
                             "span": list(span), "zitat": w["zitat"], "begruendung": w["begruendung"]})

    werte = collections.defaultdict(list)
    frei = sorted(ant["werte"], key=lambda w: FREI.index(w["feld"]))  # Ort before Land
    for f, w in [(f, w) for f in VOCAB for w in ant[f]] + [(w["feld"], w) for w in frei]:
        wert = norm_ws(w["wert"])
        if not wert:
            continue
        quelle, span = belegt(w, mit_kette if f in {"ort", "land"} else quellen)
        fehler = (
            "Zitat nicht in der Quelle" if span is None
            else "Typ schon durch Regel gesetzt" if f == "typ" and ds["Typ"]
            else "Name steht nicht im Merkmal" if f in {"urheber", "besitz"} and not named_in(wert, mark_text)
            and not (f == "urheber" and artist and named_in(wert, artist))
            else "Land ohne belegten Ort" if f == "land" and not werte["ort"] and not named_in(wert, mark_text)
            else None)
        if fehler:
            probleme.append((mid, "abgelehnt", FELDER[f], wert, fehler, w["zitat"]))
            continue
        if w.get("erschlossen"):
            wert += " (erschlossen)"
        if wert not in werte[f]:
            werte[f].append(wert)
            trace(FELDER[f], wert, w, quelle, span)
    for d in ant["daten"]:
        gelesen, iso = norm_ws(d["wie_gelesen"]), norm_ws(d["iso"])
        quelle, span = belegt(d, quellen)
        fehler = ("Zitat nicht in der Quelle" if span is None
                  else "kein ISO-8601-2-Datum" if not ISO_DATE.match(iso) else None)
        if fehler:
            probleme.append((mid, "abgelehnt", "Datum", f"{gelesen} → {iso}", fehler, d["zitat"]))
        elif (gelesen, iso) not in zip(werte["datum_wie_gelesen"], werte["datum_iso"]):
            werte["datum_wie_gelesen"].append(gelesen)
            werte["datum_iso"].append(iso)
            trace(FELDER["datum_wie_gelesen"], gelesen, d, quelle, span)
            trace(FELDER["datum_iso"], iso, d, quelle, span)
    for f, vs in werte.items():
        ds[FELDER[f]] = "; ".join(vs)

    notes = [ds["Bemerkungen"]] if ds["Bemerkungen"] else []
    for b in ant["bemerkungen"]:
        quelle, span = belegt(b, quellen)
        if span is None:
            probleme.append((mid, "verworfen", "Bemerkungen", b["text"], "Zitat nicht in der Quelle", b["zitat"]))
            continue
        cols = ", ".join(dict.fromkeys(FELDER[f] for f in b["felder"]))
        text = f"{cols}: {norm_ws(b['text'])}" if cols else norm_ws(b["text"])
        notes.append(text)
        rec["trace"].append({"feld": "Bemerkungen", "wert": text, "methode": "llm", "modell": model,
                             "quelle": quelle, "span": list(span), "zitat": b["zitat"],
                             "begruendung": "Zweifel oder Fehler in der Quelle"})
    ds["Bemerkungen"] = "; ".join(notes)
    for v in ant["vokabular_vorschlaege"]:
        probleme.append((mid, "vorschlag", FELDER[v["feld"]], v["wert"], v["begruendung"], ""))
    rec["offen"]["felder"] = [c for c in rec["offen"]["felder"] if not ds[c]]


def kandidat(rec: dict) -> bool:
    return bool(rec["offen"]["llm_gruende"]) or not rec["datensatz"]["Typ"]


def stichprobe(recs: list[dict], n: int, seed: int = 1) -> list[dict]:
    """Stratified: round-robin over llm_gruende, plus a few non-candidates and untyped marks as controls."""
    rng = random.Random(seed)
    pools = collections.defaultdict(list)
    for r in recs:
        for g in r["offen"]["llm_gruende"]:
            pools[g].append(r)
        if not r["datensatz"]["Typ"]:
            pools["ohne Typ"].append(r)
        if not kandidat(r):
            pools["Kontrolle (kein Kandidat)"].append(r)
    for p in pools.values():
        rng.shuffle(p)
    chosen, invs = {}, collections.Counter()
    while len(chosen) < n and any(pools.values()):
        for p in pools.values():
            while p:
                r = p.pop()
                mid, inv = r["datensatz"]["Merkmal-ID"], r["datensatz"]["Inventarnummer"]
                if mid not in chosen and invs[inv] < 2:
                    chosen[mid] = r
                    invs[inv] += 1
                    break
            if len(chosen) >= n:
                break
    return list(chosen.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("parsed", type=Path)
    ap.add_argument("extracted", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--sample", type=int)
    ap.add_argument("--ids", nargs="+")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    data = json.loads((a.parsed / "merkmale.json").read_text())
    recs = data["merkmale"]
    prev = {}
    for i, r in enumerate(recs):
        if i and recs[i - 1]["datensatz"]["Inventarnummer"] == r["datensatz"]["Inventarnummer"]:
            prev[r["datensatz"]["Merkmal-ID"]] = recs[i - 1]
    if a.ids:
        ids = set(a.ids)
        todo = [r for r in recs if r["datensatz"]["Merkmal-ID"] in ids]
    elif a.sample:
        todo = stichprobe(recs, a.sample)
    else:
        todo = [r for r in recs if kandidat(r)]
    docs = {inv: json.loads((a.extracted / "json" / f"{inv}.json").read_text())
            for inv in {r["datensatz"]["Inventarnummer"] for r in todo}}

    prompt_sha = hashlib.sha256((SYSTEM + json.dumps(SCHEMA, sort_keys=True)).encode()).hexdigest()[:12]
    cache = a.out / "cache" / a.model
    cache.mkdir(parents=True, exist_ok=True)
    inputs = {r["datensatz"]["Merkmal-ID"]: eingabe(r, prev.get(r["datensatz"]["Merkmal-ID"]),
                                                     docs[r["datensatz"]["Inventarnummer"]]) for r in todo}

    keys = {mid: hashlib.sha256(f"{prompt_sha}{a.effort}{json.dumps(inp, sort_keys=True)}".encode()).hexdigest()
            for mid, inp in inputs.items()}

    def cache_file(mid):
        return cache / f"{mid.replace('#', '_')}.json"

    results, missing = {}, []
    for mid in inputs:
        f = cache_file(mid)
        if f.exists() and (c := json.loads(f.read_text())).get("key") == keys[mid]:
            results[mid] = c
        else:
            missing.append(mid)
    print(f"{len(todo)} marks, {len(results)} cached, {len(missing)} to call ({a.model}, effort {a.effort})",
          file=sys.stderr)
    errors = {}
    if missing:
        load_key()
        client = anthropic.Anthropic(max_retries=4)
        with cf.ThreadPoolExecutor(a.workers) as ex:
            # first call alone, so the others read the system prompt from the cache
            first = ex.submit(call, client, a.model, a.effort, inputs[missing[0]])
            cf.wait([first])
            futs = {first: missing[0]}
            futs |= {ex.submit(call, client, a.model, a.effort, inputs[mid]): mid for mid in missing[1:]}
            for n, fut in enumerate(cf.as_completed(futs), 1):
                mid = futs[fut]
                try:
                    res = fut.result() | {"key": keys[mid], "eingabe": inputs[mid]}
                except (anthropic.APIError, RuntimeError, json.JSONDecodeError) as e:
                    errors[mid] = f"{type(e).__name__}: {e}"
                    continue
                cache_file(mid).write_text(
                    json.dumps(res, ensure_ascii=False, indent=1))
                results[mid] = res
                if n % 20 == 0:
                    print(f"  {n}/{len(missing)}", file=sys.stderr)

    probleme = [(mid, "fehler", "", "", e, "") for mid, e in errors.items()]
    for r in todo:
        mid = r["datensatz"]["Merkmal-ID"]
        if mid in results:
            merge(r, inputs[mid], results[mid], a.model, probleme)
    # a full run passes non-candidates through, so OUT holds the whole dataset
    out_recs = todo if (a.ids or a.sample) else recs
    usage = collections.Counter()
    for res in results.values():
        usage.update(res["usage"])

    lauf = data["lauf"]
    for s in lauf["schichten"]:
        if s["methode"] == "llm":
            s.update({"status": "gelaufen", "skript": "scripts/llm_merkmale.py",
                      "skript_sha256": sha(Path(__file__).resolve()), "modell": a.model,
                      "effort": a.effort, "prompt_sha256": prompt_sha, "merkmale": len(results),
                      "lauf": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "merkmale.json").write_text(json.dumps({"lauf": lauf, "merkmale": out_recs}, ensure_ascii=False, indent=1))
    write_csv(a.out / "merkmale.csv", out_recs)
    with open(a.out / "llm_probleme.tsv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["Merkmal-ID", "Art", "Spalte", "Wert", "Grund", "Zitat"])
        w.writerows(sorted(probleme, key=lambda p: (p[1], p[0])))

    fill = collections.Counter(t["feld"] for r in out_recs for t in r["trace"] if t["methode"] == "llm")
    print("filled:", ", ".join(f"{c} {fill[c]}" for c in FELDER.values()), file=sys.stderr)
    print("problems:", dict(collections.Counter(p[1] for p in probleme)), file=sys.stderr)
    print(f"tokens: {dict(usage)}", file=sys.stderr)


if __name__ == "__main__":
    main()
