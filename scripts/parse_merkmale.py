# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Rule layer of the provenance-mark parser (schema: merkmal-schema.md).

Usage: uv run scripts/parse_merkmale.py EXTRACTED_DIR OUT_DIR

Fills the physical-description columns (Typ, Technik, Form, Rand / Ecken, colours,
Schreibmittel, Anbringungsort) from fixed rules and leaves the interpretive columns
for the LLM layer. Every value carries a trace entry (rule id and character span in
Originalbeschreibung) so that later HTML trace pages can show what came from which rule.

Writes OUT_DIR/merkmale.json (run metadata + records + traces), OUT_DIR/merkmale.csv
and OUT_DIR/unbekannt.tsv (words outside transcriptions that no rule explained).
"""
from __future__ import annotations

import collections
import csv
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

COLUMNS = [
    "Merkmal-ID", "Quellzeile", "Inventarnummer", "Foto", "Originalbeschreibung", "Typ", "Technik",
    "Form", "Rand / Ecken", "Hintergrundfarbe", "Farbe der Umrandung", "Farbe der Schrift",
    "Schreibmittel", "Anbringungsort", "Art des Urhebers", "Urheber (normalisiert)", "Ort", "Land",
    "Ereignis", "Besitz", "Ausstellung", "Datum (wie gelesen)", "Datum (ISO 8601-2:2019)", "Sprache",
    "Bemerkungen",
]
# physical columns a cross-reference ("siehe vorausgehendes Provenienzmerkmal") takes over
COPIED = ["Typ", "Technik", "Form", "Rand / Ecken", "Hintergrundfarbe", "Farbe der Umrandung", "Farbe der Schrift",
          "Schreibmittel"]
LLM_COLUMNS = [
    "Art des Urhebers", "Urheber (normalisiert)", "Ort", "Land", "Ereignis", "Besitz", "Ausstellung",
    "Datum (wie gelesen)", "Datum (ISO 8601-2:2019)", "Sprache",
]

# ---------------------------------------------------------------- vocabulary

W = r"[A-Za-zÄÖÜäöüß]"
INFL = r"(?:e|er|es|em|en|n|r|s)?"
COLOUR_BASE = (r"(?:(?:hell|dunkel|blass|tief)-?)?(?:weiss|schwarz|rot|blau|grün|gelb|braun|beige|grau|orange|"
               r"violett|rosa|pink|magenta|gold|silber|ocker|lila|türkis|creme)(?:braun|rot|blau|grün|gelb|grau|schwarz)?")
COLOUR = rf"(?:{COLOUR_BASE}|rötlich|bräunlich|gelblich|gräulich|bläulich|dunkl|dunkel|hell){INFL}"
COLOUR_RUN = rf"{COLOUR}(?:(?:\s*(?:,|und|oder|bzw\.)\s*|-){COLOUR})*"
FORM_WORD = rf"(?:lang)?(?:rechteckig|viereckig|rund|oval){INFL}"

# Technik: (rule id, value, pattern). Wachsstempel is a seal, not a stamp.
TECHNIK = [
    ("technik.siegel", "Siegel", rf"\b(?:Wachs)?[Ss]iegel{W}*|\bWachsstempel{W}*"),
    ("technik.maschinenschriftlich", "maschinenschriftlich",
     rf"\bmaschinen(?:schriftlich|beschrieben|geschrieben|beschriftet){W}*|\bMaschinenschrift{W}*"),
    ("technik.gedruckt", "gedruckt", rf"\b(?:ge|be)druckt{W}*|\bDruck\b"),
    ("technik.gestempelt", "gestempelt", rf"\bgestempelt{W}*|\b(?!Wachsstempel){W}*[Ss]tempel{W}*"),
    ("technik.graviert", "graviert", rf"\b(?:ein)?graviert{W}*"),
    ("technik.eingeritzt", "eingeritzt", rf"\b(?:ein)?(?:geritzt|gekratzt){W}*"),
    ("technik.schabloniert", "schabloniert", rf"\bschabloniert{W}*|\b{W}*[Ss]chablone{W}*"),
    ("technik.handschriftlich", "handschriftlich", rf"\bhandschriftlich{W}*|\bhand(?:beschrieben|geschrieben){W}*"),
    ("technik.beschrieben", "handschriftlich", r"\bbeschrieben\b|\bbeschriftet\b"),
]
# naming a writing medium implies handwriting: "Reste einer Aufschrift mit weisser Kreide"
MEDIUM_NOUN = (r"(?:Schreibmittel|Bleistift|Kreide|Farbstift|Wachsstift|Wachskreide|Wachsfarbe|Tinte|Kugelschreiber|"
               r"Filzstift|Fettstift|Buntstift|Stift|Farbe|Pinsel|Tusche)\w*")
FORM = [
    ("form.langrechteckig", "langrechteckig", rf"\blangrechteckig{INFL}\b"),
    ("form.rechteckig", "rechteckig", rf"\brechteckig{INFL}\b"),
    ("form.viereckig", "viereckig", rf"\bviereckig{INFL}\b"),
    ("form.quadratisch", "quadratisch", rf"\bquadratisch{INFL}\b"),
    ("form.hochrechteckig", "hochrechteckig", rf"\bhochrechteckig{INFL}\b"),
    ("form.achteckig", "achteckig", rf"\bachteckig{INFL}\b"),
    ("form.wappenfoermig", "wappenförmig", rf"\bwappenförmig{INFL}\b"),
    ("form.rund", "rund", rf"\brund{INFL}\b"),
    ("form.oval", "oval", rf"\boval{INFL}\b"),
]
RAND = [
    ("rand.abgerundet", "abgerundete Ecken", r"\babgerundete[nm]?\s+Ecken\b"),
    ("rand.gezackt", "gezackter Rand", r"\bgezackte[nmr]?\s+Rand\w*"),
    ("rand.gezahnt", "gezackter Rand", r"\bgezahnt\w*(?:\s+R[aä]nd\w*)?"),
    ("rand.perforiert", "perforierter Rand", r"\bperforierte[nmr]?\s+(?:Rand|Kante)\w*"),
]
SCHREIBMITTEL = {"bleistift": "Bleistift", "kreide": "Kreide", "farbstift": "Farbstift",
                 "wachsstift": "Wachsstift", "wachskreide": "Wachsstift", "wachsfarbe": "Wachsstift",
                 "tinte": "Tinte", "kugelschreiber": "Kugelschreiber", "pinsel": "Pinsel",
                 "filzstift": "Filzstift", "fettstift": "Fettstift", "stift": "Stift"}
# Nouns that mean "some writing medium" without naming one; not reported as unknown.
GENERIC_MEDIUM = {"schreibmittel", "schrift"}

# Typ, in precedence order: the first rule that matches decides.
TYP = [
    ("typ.reste", "Reste entfernter Provenienzmerkmale", rf"\b{W}*[Rr]este[n]?\b|\bRest\b|\bNegativ\w*|\bFragment\w*"),
    ("typ.plakette", "Plaketten", rf"\b{W}*(?:[Pp]lakette|[Ss]child){W}*"),
    ("typ.etikett", "Etiketten", rf"\b{W}*(?:[Ee]tikett|[Zz]ettel|Klebepunkt|Aufkleber){W}*"),
    ("typ.stempel", "Stempel", rf"\b{W}*(?:[Ss]tempel|[Ss]iegel){W}*"),
    ("typ.signatur", "Inschriften", rf"\b(?:Signatur|Monogramm){W}*"),
    ("typ.eingeritzt", "Inschriften", rf"\b(?:ein)?(?:geritzt|gekratzt){W}*"),
    ("typ.markierung", "Markierungen", rf"\bMarkierung{W}*|\bPunkt{W}*"),
    ("typ.malerband", "Beschriftungen", rf"\b(?:Malerband|Malerklebeband|Klebstreifen|Klebeband){W}*"),
    ("typ.notiz", "Beschriftungen", rf"\b{W}*(?:[Nn]otiz|[Nn]otat|[Vv]ermerk|[Aa]ufschrift){W}*"),
]
LABEL_NOUN = rf"(?:{W}+-)?{W}*(?:[Ee]tikett|[Zz]ettel|Papier|Klebepunkt|Blatt|Malerband|Klebstreifen|Klebeband|Karte|Kärtchen){W}*"
STAMP_NOUN = rf"{W}*(?:[Ss]tempel|[Ss]iegel|Stempelabdruck){W}*"
TECH_VERB = (r"(?:ge|be)druckt\w*|gestempelt|beschrieben|beschriftet|maschinenbeschrieben|maschinengeschrieben|"
             r"handschriftlich\w*|geschrieben|schabloniert|graviert|eingraviert|überschrieben|ergänzt|annotiert")
# Not recorded (schema): condition words and later changes to a mark. Listed so the report stays readable.
IGNORED = re.compile(r"verbräun\w*|vergilbt\w*|(?:durch|unter|an)gestrichen\w*|(?:ein|um)gekreist\w*|umkreist\w*", re.I)

# photocopies, catalogue extracts and other documents attached to the work (schema: Typ «Dokumente»)
DOKUMENT = r"\b(?:[Ff]oto)?[Kk]opie\w*|\b(?:foto)?kopiert\w*|\bDokument\w*|\bAus(?:zug|schnitt)\w*|\bZeitung\w*"

DOUBT = r"(?:vermutlich|möglicherweise|wohl|allenfalls|eventuell|evtl\.|wahrscheinlich|eher)"

STOP = set("""
a am an auf aus bei bis bzw d dabei daneben dann darauf darin darüber darunter das dass davon dazu dem den der
des die dies diese diesem diesen dieser drei ein eine einem einen einer eines einige er es etwa für ganz im in
ist je jeweils l links m mit mitte nach neben nicht nur o ob oben oder r rechts s sich sie sind so sowie teils
teilweise u über um und unten unter unterhalb oberhalb vom von vor wie z zu zum zur zwei zwischen sowohl als
auch beide beiden beidseitig ohne etc weitere weiteren weiterer weiteres davor dahinter ebenfalls jedoch
""".split())

CUES = {
    "Auktion": r"Auktion|\bLot\b|Vente|Versteiger|Sotheby|Christie|Fischer|Koller|Dobiaschofsky|Kornfeld",
    "Kunsthandel": r"Galerie|Kunsthandl|Gallery|Galleria|Kunstsalon",
    "Ausstellung": r"Ausstell|Exposition|Exhibition|Esposizione|Mostra|Leihgabe|Leihgeber",
    "Zoll": r"Zoll|Douane|Dogana|Customs",
    "Institution": r"Museum|Kunsthaus|Kunsthalle|Kunstverein|SIK|ISEA",
    "Besitz": r"Sammlung|Nachlass|Collection|Collezione|Besitz|Eigentum|erworben|Erworben",
}


# ---------------------------------------------------------------- helpers

def mask(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Blank out «…» transcriptions, […] and (…) keeping offsets; return masked text and transcription spans."""
    out, quotes = list(text), []
    pairs = {"«": "»", "[": "]", "(": ")"}
    i = 0
    while i < len(text):
        o = text[i]
        if o in pairs:
            c, depth, j = pairs[o], 0, i
            while j < len(text):
                if text[j] == o:
                    depth += 1
                elif text[j] == c:
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            end = min(j + 1, len(text))
            if o == "«":
                quotes.append((i, end))
            for k in range(i, end):
                out[k] = " "
            i = end
        else:
            i += 1
    return "".join(out), quotes


def colour_norm(s: str) -> str:
    s = s.lower()
    m = re.match(rf"({COLOUR_BASE}|rötlich|bräunlich|gelblich|gräulich|bläulich|dunkl|dunkel|hell)", s)
    c = m.group(1).replace("-", "") if m else s
    return "dunkel" if c == "dunkl" else c


def colours_in(run: str) -> list[str]:
    return [colour_norm(m.group(0)) for m in re.finditer(COLOUR, run, re.I)]


class Mark:
    def __init__(self, src: str, offset: int):
        self.src = src                       # Originalbeschreibung
        self.offset = offset                 # start of the description after "Position: "
        self.masked, self.quotes = mask(src)
        self.masked = " " * offset + self.masked[offset:]
        self.values: dict[str, list[str]] = collections.defaultdict(list)
        self.trace: list[dict] = []
        self.doubts: dict[tuple[int, int], dict] = {}
        self.covered: list[tuple[int, int]] = []
        self.colour_used: list[tuple[int, int]] = []

    def put(self, field: str, value: str, rule: str, span: tuple[int, int], doubt: bool = True):
        if value not in self.values[field]:
            self.values[field].append(value)
        self.trace.append({"feld": field, "wert": value, "methode": "regel", "regel": rule,
                           "span": list(span), "quelle": self.src[span[0]:span[1]]})
        self.covered.append(span)
        if not doubt:
            return
        window = self.masked[max(0, span[0] - 40):span[0]]
        m = re.search(rf"\b{DOUBT}\s+(?:mit\s+)?(?:(?:{COLOUR}|{FORM_WORD}|ein\w*)\s+)?$", window)
        if m:
            self.note(field, span[0] - len(window) + m.start(), span[1], "zweifel.wort")

    def note(self, field: str, start: int, end: int, rule: str):
        # passages that overlap (e.g. "vermutlich schwarz" inside "vermutlich schwarz gestempelt") merge
        for (a, b), d in list(self.doubts.items()):
            if a == start or (a <= start < b) or (start <= a < end):
                del self.doubts[(a, b)]
                start, end = min(a, start), max(b, end)
                d["felder"] = d["felder"] + [f for f in [field] if f not in d["felder"]]
                d["regeln"] = d["regeln"] + [r for r in [rule] if r not in d["regeln"]]
                self.doubts[(start, end)] = d
                return
        self.doubts[(start, end)] = {"felder": [field], "regeln": [rule]}

    def notes(self) -> list[str]:
        out = []
        for (a, b), d in sorted(self.doubts.items()):
            text = f"{', '.join(d['felder'])}: {self.src[a:b].strip()}, unsicher"
            out.append(text)
            self.trace.append({"feld": "Bemerkungen", "wert": text, "methode": "regel", "regel": "; ".join(d["regeln"]),
                               "span": [a, b], "quelle": self.src[a:b]})
        return out

    def drop(self, rule: str):
        gone = [t for t in self.trace if t["regel"] == rule]
        self.trace = [t for t in self.trace if t["regel"] != rule]
        for t in gone:
            if not any(u["feld"] == t["feld"] and u["wert"] == t["wert"] for u in self.trace):
                self.values[t["feld"]].remove(t["wert"])

    def finditer(self, pattern: str, flags=0):
        return re.finditer(pattern, self.masked, flags)


# ---------------------------------------------------------------- rules

def apply_rules(mk: Mark, position: str):
    if position:
        mk.put("Anbringungsort", position, "anbringungsort.position", (0, len(position)))
    for m in mk.finditer(rf"\bangeheftet{W}*"):
        mk.put("Anbringungsort", "angeheftet", "anbringungsort.angeheftet", m.span())

    for table, field in ((TECHNIK, "Technik"), (FORM, "Form"), (RAND, "Rand / Ecken")):
        claimed: list[tuple[int, int]] = []
        for rule, value, pat in table:
            for m in mk.finditer(pat):
                if any(a <= m.start() < b for a, b in claimed):
                    continue                 # e.g. "langrechteckig" already taken, or Wachsstempel as Siegel
                # only lowercase words in between, so "rundes Etikett mit Symbol" still counts as Form
                if field == "Form" and re.match(r"(?:[\s,]+[a-zäöüß][\w-]*)*?[\s,]+(?:Punkt|Symbol|Zeichen|Markierung|Kreis)",
                                                mk.masked[m.end():]):
                    continue
                claimed.append(m.span())
                mk.put(field, value, rule, m.span())

    head = re.match(r"[^,;«]*", mk.masked[mk.offset:])
    head_end = mk.offset + (head.end() if head else 0)
    for m in mk.finditer(rf"\bmit\s+(?:{COLOUR}\s+)?{MEDIUM_NOUN}"):
        mk.put("Technik", "handschriftlich", "technik.schreibmittel", m.span(), doubt=False)
    if any(t["regel"] not in ("technik.beschrieben", "technik.handschriftlich", "technik.schreibmittel")
           for t in mk.trace if t["feld"] == "Technik"):
        mk.drop("technik.beschrieben")

    body = mk.masked[mk.offset:]
    erwerb = re.match(r"\s*(Erwerbsnachweis\w*)", body)
    # only «…» blanked here, so "(Kopie)" counts; first two clauses, so "gemäss Auktionskatalog …" later does not
    unquoted = "".join(" " if any(a <= i < b for a, b in mk.quotes) else ch for i, ch in enumerate(mk.src))
    two = re.match(r"[^,;]*(?:[,;][^,;]*)?", unquoted[mk.offset:])
    doc = re.search(DOKUMENT, two.group(0)) if two else None
    reste = re.match(r"[^,;«]*?\b(?:Klebe|Über)?[Rr]est", body)
    if erwerb:
        mk.put("Typ", "Erwerbsnachweise", "typ.erwerbsnachweis",
               (mk.offset + erwerb.start(1), mk.offset + erwerb.end(1)))
    elif doc and not reste:
        mk.put("Typ", "Dokumente", "typ.dokument", (mk.offset + doc.start(), mk.offset + doc.end()))
    for rule, value, pat in ([] if mk.values["Typ"] else TYP):
        m = next((m for m in mk.finditer(pat) if m.start() < head_end), None)
        if m:
            mk.put("Typ", value, rule, m.span())
            break
    else:
        if mk.values["Typ"]:
            pass
        elif m := next(mk.finditer(r"\bhandschriftlich\w*|\bmaschinen\w+|\b(?:ge|be)druckt\w*|\bbeschrieben\b|\bschabloniert\w*|\w*[Ss]chablone\w*|\bhand(?:be|ge)schrieben\w*|"
                         rf"\bmit\s+(?:{COLOUR}\s+)?{MEDIUM_NOUN}"), None):
            mk.put("Typ", "Beschriftungen", "typ.beschriftung", m.span())
        else:                                # nothing in the first clause: fall back to the whole text
            for rule, value, pat in TYP:
                m = next(mk.finditer(pat), None)
                if m:
                    mk.put("Typ", value, rule, m.span())
                    break
    mk.covered += [m.span() for _, _, pat in TYP for m in mk.finditer(pat)]

    # Schreibmittel: suspended compounds first ("Farb- oder Wachsstift")
    for m in mk.finditer(rf"\b({W}+)-\s+(oder|und)\s+({W}+?)(stift|kreide|farbe)\b"):
        a = SCHREIBMITTEL.get((m.group(1) + m.group(4)).lower(), None)
        b = SCHREIBMITTEL.get((m.group(3) + m.group(4)).lower(), None)
        for v in (a, b):
            if v:
                mk.put("Schreibmittel", v, "schreibmittel.ellipse", m.span())
        if m.group(2) == "oder":
            mk.note("Schreibmittel", m.start(), m.end(), "schreibmittel.ellipse")
    for m in mk.finditer(rf"\b{W}+\b"):
        w = m.group(0).lower()
        v = SCHREIBMITTEL.get(w) or next((SCHREIBMITTEL[k] for k in SCHREIBMITTEL      # "Bleistiftnotiz"
                                          if k != "stift" and len(w) > len(k) + 2 and w.startswith(k)), None)
        if v and not any(t["regel"] == "schreibmittel.ellipse" and t["span"][0] <= m.start() < t["span"][1]
                         for t in mk.trace):
            mk.put("Schreibmittel", v, f"schreibmittel.{m.group(0).lower()}", m.span())

    if mk.values["Schreibmittel"] and not mk.values["Technik"]:     # "Bleistiftnotiz"
        t = next(t for t in mk.trace if t["feld"] == "Schreibmittel")
        mk.put("Technik", "handschriftlich", "technik.aus_schreibmittel", tuple(t["span"]), doubt=False)

    colour_rules = [
        ("farbe.umrandung", "Farbe der Umrandung", rf"(?P<c>{COLOUR_RUN})\s+(?:{W}+(?:er|en|e)\s+)?(?:Umrandung|Bordüre|Rahmung|Rahmenlinie|Rand)\w*|(?P<c2>{COLOUR_RUN})\s+umrandet"),
        ("farbe.hintergrund", "Hintergrundfarbe", rf"(?P<c>{COLOUR_RUN})\s+(?:{FORM_WORD}\s+)?(?:Klebe)?{LABEL_NOUN}"),
        ("farbe.stempel", "Farbe der Schrift", rf"(?P<c>{COLOUR_RUN})\s+(?:{FORM_WORD}\s+)?{STAMP_NOUN}"),
        ("farbe.technik", "Farbe der Schrift", rf"(?P<c>{COLOUR_RUN})\s+(?:{TECH_VERB})\b"),
        ("farbe.schreibmittel", "Farbe der Schrift", rf"\b(?:mit|und|oder)\s+(?P<c>{COLOUR_RUN})\s+(?P<n>{MEDIUM_NOUN}|{W}+-\s+(?:oder|und)\s+{MEDIUM_NOUN})"),
        ("farbe.in", "Farbe der Schrift", rf"\bin\s+(?P<c>{COLOUR_RUN})\b"),
    ]
    for rule, field, pat in colour_rules:
        for m in mk.finditer(pat, re.I if rule == "farbe.in" else 0):
            g = "c" if m.group("c") else "c2"
            span = m.span(g)
            if any(a < span[1] and span[0] < b for a, b in mk.colour_used):
                continue
            if rule == "farbe.schreibmittel" and m.group("n").lower().startswith(("umrandung", "bordüre", "rand")):
                continue
            mk.colour_used.append(span)
            for c in colours_in(m.group(g)):
                mk.put(field, c, rule, span)
            if re.search(r"\boder\b", m.group(g)):
                mk.note(field, *span, rule)
            mk.covered.append(m.span())

    # "A oder B" between two values of the same field
    by_field = collections.defaultdict(list)
    for t in mk.trace:
        if t["methode"] == "regel" and t["feld"] not in ("Bemerkungen", "Typ"):
            by_field[t["feld"]].append(t)
    for field, ts in by_field.items():
        ts = sorted(ts, key=lambda t: t["span"][0])
        for a, b in zip(ts, ts[1:]):
            gap = mk.src[a["span"][1]:b["span"][0]]
            if a["wert"] != b["wert"] and re.fullmatch(r"\s*,?\s*oder\s+(?:\S+\s+)?", gap) and len(gap) < 25:
                mk.note(field, a["span"][0], b["span"][1], "alternative.oder")


def unknown_words(mk: Mark) -> list[tuple[str, str]]:
    """Words outside transcriptions that no rule covered: (kind, word)."""
    out = []
    for m in mk.finditer(rf"{W}+(?:-{W}+)*"):
        w = m.group(0)
        if any(a <= m.start() < b for a, b in mk.covered) or w.lower() in STOP or len(w) < 2:
            continue
        lw = w.lower()
        if IGNORED.fullmatch(w):
            continue
        if re.fullmatch(DOUBT, lw):
            kind = "Zweifelswort ohne Bezug"
        elif re.fullmatch(COLOUR, w, re.I):
            kind = "Farbe ohne Bezug"
        elif lw in GENERIC_MEDIUM:
            continue
        elif re.search(r"(stift|tinte|kreide|kugelschreiber|feder|pinsel|tusche|kohle|rötel)$", lw):
            kind = "Schreibmittel nicht im Schema"
        elif re.search(r"(eckig|förmig)\w*$", lw) or lw.startswith(("quadratisch", "dreieck", "trapez", "achteck")):
            kind = "Form nicht im Schema"
        elif re.fullmatch(r"(?:ein|auf|an|aus|ge)\w+(?:t|en)\w{0,2}", lw):
            kind = "Partizip (Technik?)"
        else:
            kind = "unbekannt"
        out.append((kind, w))
    return out


def llm_reasons(src: str, quotes: list[tuple[int, int]], kommentar: str | None) -> list[str]:
    reasons = [f"Hinweis: {k}" for k, p in CUES.items() if re.search(p, src)]
    if any(re.search(rf"{W}{{4,}}", src[a:b]) for a, b in quotes):
        reasons.append("Transkription mit Wörtern")
    if kommentar:
        reasons.append("Kommentar")
    return reasons


# ---------------------------------------------------------------- main

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def main(src: Path, dst: Path):
    dst.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve()
    schema = here.parent.parent / "merkmal-schema.md"
    records, unknown = [], collections.defaultdict(list)
    for jf in sorted((src / "json").glob("*.json")):
        inv = jf.stem
        doc = json.loads(jf.read_text(encoding="utf-8"))
        prev = None
        for m in doc["provenienzmerkmale"]["merkmale"]:
            beschr = m["beschreibung"] or ""
            pos = m.get("position_traeger") or ""
            offset = len(pos) + 2 if pos and beschr.startswith(pos + ": ") else 0
            mk = Mark(beschr, offset)
            apply_rules(mk, pos if offset else "")
            mid = f"{inv}#{m['nr']}"
            rec = {c: "" for c in COLUMNS}
            rec.update({
                "Merkmal-ID": mid, "Quellzeile": m["nr"], "Inventarnummer": inv,
                "Foto": m["bilder"][0]["datei"] if m.get("bilder") else "",
                "Originalbeschreibung": beschr,
            })
            first = {}
            for t in mk.trace:
                key = (t["feld"], t["wert"])
                first[key] = min(first.get(key, 10**6), t["span"][0])
            for field, vals in mk.values.items():
                rec[field] = "; ".join(sorted(vals, key=lambda v: first.get((field, v), 0)))
            rec["Bemerkungen"] = "; ".join(mk.notes())
            ref = re.fullmatch(r"siehe vorausgehendes Provenienzmerkmal\.?", beschr[offset:].strip())
            if ref and prev:
                span = [offset, len(beschr)]
                for c in COPIED:
                    rec[c] = prev["datensatz"][c]
                    if rec[c]:
                        mk.trace.append({"feld": c, "wert": rec[c], "methode": "verweis", "regel": "verweis.vorausgehend",
                                         "von": prev["datensatz"]["Merkmal-ID"], "span": span, "quelle": beschr[offset:]})
                rec["Bemerkungen"] = f"Physische Angaben aus {prev['datensatz']['Merkmal-ID']} übernommen (Verweis)"
                mk.trace.append({"feld": "Bemerkungen", "wert": rec["Bemerkungen"], "methode": "verweis",
                                 "regel": "verweis.vorausgehend", "span": span, "quelle": beschr[offset:]})
            reasons = llm_reasons(beschr, mk.quotes, m.get("kommentar"))
            for kind, w in unknown_words(mk):
                unknown[(kind, w)].append(mid)
            prev = {
                "datensatz": rec,
                "trace": sorted(mk.trace, key=lambda t: (t["span"][0], t["feld"])),
                "kontext": {"seite": m.get("seite"), "kommentar": m.get("kommentar"),
                            "transkriptionen": [beschr[a:b] for a, b in mk.quotes]},
                "offen": {"felder": LLM_COLUMNS, "llm_gruende": reasons},
            }
            records.append(prev)

    run = {
        "parser": "scripts/parse_merkmale.py", "parser_sha256": sha(here),
        "schema": "merkmal-schema.md", "schema_sha256": sha(schema) if schema.exists() else None,
        "eingabe": str(src / "json"), "lauf": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "schichten": [{"methode": "regel", "felder": [c for c in COLUMNS if c not in LLM_COLUMNS]},
                      {"methode": "llm", "felder": LLM_COLUMNS, "status": "noch nicht gelaufen"}],
    }
    (dst / "merkmale.json").write_text(json.dumps({"lauf": run, "merkmale": records}, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    with (dst / "merkmale.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS + ["LLM-Gründe"])
        w.writeheader()
        for r in records:
            w.writerow({**r["datensatz"], "LLM-Gründe": "; ".join(r["offen"]["llm_gruende"])})
    with (dst / "unbekannt.tsv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["art", "wort", "anzahl", "beispiele"])
        for (kind, word), ids in sorted(unknown.items(), key=lambda kv: (kv[0][0], -len(kv[1]), kv[0][1])):
            w.writerow([kind, word, len(ids), " ".join(ids[:5])])

    n = len(records)
    print(f"{n} Merkmale")
    for c in ("Typ", "Technik", "Form", "Rand / Ecken", "Hintergrundfarbe", "Farbe der Umrandung",
              "Farbe der Schrift", "Schreibmittel", "Anbringungsort", "Bemerkungen"):
        k = sum(bool(r["datensatz"][c]) for r in records)
        print(f"  {c:<22} {k:>5} ({100 * k / n:.0f}%)")
    llm = sum(bool(r["offen"]["llm_gruende"]) for r in records)
    print(f"  LLM-Kandidaten         {llm:>5} ({100 * llm / n:.0f}%)")
    kinds = collections.Counter()
    for (kind, _), ids in unknown.items():
        kinds[kind] += len(ids)
    print("  unerklärte Wörter:", dict(kinds.most_common()))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
