# /// script
# requires-python = ">=3.11"
# ///
"""Render one static HTML trace page per provenance mark.

    uv run scripts/trace_pages.py parsed/llm extracted trace --ids 4084#3 1669#3

PARSED_DIR holds merkmale.json (and llm_probleme.tsv, if any); pages go to
OUT/<inv>-<nr>.html and link to ../extracted and ../Provenienz_Datenblatt, so
OUT must sit in the repository root.
"""
import argparse
import csv
import html
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

REPO = "https://github.com/kintopp/glamhack26-prov-parser"
SKRIPTE = ["scripts/parse_merkmale.py", "scripts/llm_merkmale.py"]
NAMEN = "namen-normalisierung.tsv"

PHYSISCH = ["Typ", "Technik", "Form", "Rand / Ecken", "Hintergrundfarbe", "Farbe der Umrandung",
            "Farbe der Schrift", "Schreibmittel", "Anbringungsort"]
DEUTUNG = ["Art des Urhebers", "Urheber (normalisiert)", "Ort", "Land", "Ereignis", "Besitz", "Ausstellung",
           "Datum (wie gelesen)", "Datum (ISO 8601-2:2019)", "Sprache"]
METHODE = {"regel": "Regel", "verweis": "Verweis", "llm": "LLM", "foto": "LLM · Foto",
           "normalisierung": "Normalisierung"}
QUELLE = {"beschreibung": "Beschreibung", "kommentar": "Kommentar", "provenienzkette": "Provenienzkette",
          "foto": "Foto"}
# llm_probleme.tsv names the date columns together
SPALTE = {"Datum": ["Datum (wie gelesen)", "Datum (ISO 8601-2:2019)"]}

e = html.escape

# filled by quellorte(): rule id -> link to the line that sets it; name variant -> (link, decision date)
REGEL_LINK: dict[str, str] = {}
NAMEN_ZEILE: dict[str, tuple[str, str]] = {}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def quellorte(root: Path, ids: set[str]) -> None:
    """Find each rule id in the scripts at HEAD, and when each name-table row was committed."""
    head = git("-C", str(root), "rev-parse", "HEAD").strip()
    if git("-C", str(root), "status", "--porcelain", "--", *SKRIPTE, NAMEN).strip():
        print(f"warning: scripts or {NAMEN} differ from HEAD; links point at HEAD")
    zeilen = {f: (root / f).read_text().splitlines() for f in SKRIPTE}
    for rid in ids:
        # literal id first, then an f-string prefix such as f"schreibmittel.{…}"
        prefix = rid.split(".")[0]
        for muster in (f'"{rid}"', f'f"{prefix}.{{'):
            hit = next(((f, n) for f, ls in zeilen.items() for n, l in enumerate(ls, 1) if muster in l), None)
            if hit:
                REGEL_LINK[rid] = f"{REPO}/blob/{head}/{hit[0]}#L{hit[1]}"
                break
    zeit = {}
    for block in git("-C", str(root), "blame", "--line-porcelain", NAMEN).split("\n\t"):
        m = re.search(r"^[0-9a-f]{40} \d+ (\d+)", block, re.M)
        c = re.search(r"^committer-time (\d+)", block, re.M)
        if m and c:
            zeit[int(m.group(1))] = datetime.fromtimestamp(int(c.group(1))).strftime("%d.%m.%Y")
    for n, line in enumerate((root / NAMEN).read_text().splitlines(), 1):
        variante = line.split("\t")[0]
        if n > 1 and variante:
            NAMEN_ZEILE[variante] = (f"{REPO}/blob/{head}/{NAMEN}#L{n}", zeit.get(n, ""))


def page_name(mid: str) -> str:
    return mid.replace("#", "-") + ".html"


def kette(doc: dict) -> list[str]:
    # same text as llm_merkmale.kette(), so provenienzkette spans line up
    return [" ".join(f"{p.get('zeitraum') or ''}: {p.get('besitzer') or ''}".split())
            for p in doc.get("provenienz") or []]


def art(t: dict) -> str:
    if t["methode"] == "llm" and t.get("quelle") == "foto":
        return "foto"
    return t["methode"]


def text_von(t: dict) -> str | None:
    """Which source text a trace's span points into."""
    if t.get("span") is None:
        return None
    if t["methode"] in ("regel", "verweis") and t.get("quelle") not in QUELLE:
        return "beschreibung"  # rule traces keep the matched text in `quelle`
    return t.get("quelle")


def markiert(text: str, spans: list[tuple[int, int, int, str]]) -> str:
    """Text with every traced span wrapped; overlapping spans share segments."""
    if not text:
        return ""
    cuts = sorted({0, len(text)} | {p for s, x, *_ in spans for p in (s, x)})
    out = []
    for a, b in zip(cuts, cuts[1:]):
        hits = [(i, m) for s, x, i, m in spans if s <= a and b <= x]
        seg = e(text[a:b]).replace("\n", "<br>")
        if hits:
            arten = sorted({m for _, m in hits})
            ids = " ".join(f"t{i}" for i, _ in hits)
            seg = f'<span class="hl {" ".join("hl-" + m for m in arten)}" data-t="{ids}">{seg}</span>'
        out.append(seg)
    return "".join(out)


def zitat_html(z: str) -> str:
    # transcriptions already carry their own «…»
    return f'<q class="zitat{" roh" if z.startswith("«") else ""}">{e(z)}</q>'


def beleg(t: dict, i: int) -> str:
    q = t.get("quelle")
    if t["methode"] == "normalisierung":
        return f'<span class="von">{e(t["von"])}</span> → <span class="nach">{e(t["wert"])}</span>'
    zitat = t.get("zitat") or (q if t["methode"] in ("regel", "verweis") and q not in QUELLE else None)
    wo = QUELLE.get(q, "Beschreibung")
    if t.get("von"):
        wo += f' · aus <a href="{page_name(t["von"])}">{e(t["von"])}</a>'
    teile = [f'<span class="wo">{e(wo)}</span>']
    if zitat:
        teile.append(zitat_html(zitat))
    return " ".join(teile)


def erklaerung(t: dict) -> str:
    if t["methode"] == "llm":
        return e(t.get("begruendung") or "")
    if t["methode"] == "normalisierung":
        link, datum = NAMEN_ZEILE.get(t["von"], ("", ""))
        code = f'<code>{e(NAMEN)}</code>'
        return (f'<a href="{e(link)}">{code}</a>' if link else code) + (f" · entschieden {datum}" if datum else "")
    rid = t.get("regel") or ""
    code = f'<code>{e(rid)}</code>'
    return f'<a href="{e(REGEL_LINK[rid])}">{code}</a>' if rid in REGEL_LINK else code


def schritt(t: dict, i: int, cls: str = "") -> str:
    m = art(t)
    return (f'<li class="schritt {cls}" data-t="t{i}" id="t{i}">'
            f'<span class="m m-{m}">{METHODE[m]}</span>'
            f'<span class="wert">{e(t["wert"])}</span>'
            f'<span class="beleg">{beleg(t, i)}</span>'
            f'<span class="warum">{erklaerung(t)}</span></li>')


def zeile(feld: str, wert: str, traces: list[tuple[int, dict]], abgelehnt: list[dict]) -> str:
    leer = not wert
    werte = "".join(f'<span class="chip">{e(v)}</span>' for v in wert.split("; ")) if wert else "—"
    schritte = "".join(schritt(t, i) for i, t in traces)
    for p in abgelehnt:
        schritte += (f'<li class="schritt abgelehnt"><span class="m m-abgelehnt">abgelehnt</span>'
                     f'<span class="wert">{e(p["Wert"])}</span>'
                     f'<span class="beleg">{zitat_html(p["Zitat"]) if p["Zitat"] else ""}</span>'
                     f'<span class="warum">{e(p["Grund"])}</span></li>')
    return (f'<tr class="{"leer" if leer and not schritte else ""}"><th scope="row">{e(feld)}</th>'
            f'<td class="final">{werte}</td><td><ul class="schritte">{schritte}</ul></td></tr>')


def tabelle(titel: str, hinweis: str, felder: list[str], ds: dict, by_feld: dict, prob: dict) -> str:
    rows = "".join(zeile(f, ds.get(f, ""), by_feld.get(f, []), prob.get(f, [])) for f in felder)
    return (f'<section class="karte"><h2>{titel}</h2><p class="hinweis">{hinweis}</p>'
            f'<table class="werte"><thead><tr><th>Spalte</th><th>Wert</th><th>Herleitung</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></section>')


def render(rec: dict, doc: dict, lauf: dict, prob_rows: list[dict], nav: tuple) -> str:
    ds, kx, offen = rec["datensatz"], rec["kontext"], rec["offen"]
    mid, inv = ds["Merkmal-ID"], ds["Inventarnummer"]
    traces = list(enumerate(rec["trace"]))
    by_feld: dict[str, list] = {}
    for i, t in traces:
        by_feld.setdefault(t["feld"], []).append((i, t))
    prob: dict[str, list] = {}
    for p in prob_rows:
        for f in SPALTE.get(p["Spalte"], [p["Spalte"]]):
            prob.setdefault(f, []).append(p)

    kette_text = "\n".join(kette(doc))
    texte = {"beschreibung": ds["Originalbeschreibung"], "kommentar": kx["kommentar"] or "",
             "provenienzkette": kette_text}
    spans = {k: [] for k in texte}
    for i, t in traces:
        k = text_von(t)
        if k in spans:
            spans[k].append((*t["span"], i, art(t)))

    o = doc.get("objekt") or {}
    pdf = f'../Provenienz_Datenblatt/{doc.get("datei", "")}'
    fotos = [f for f in ds["Foto"].split("; ") if f]
    foto_html = "".join(f'<a href="../extracted/{e(f)}"><img src="../extracted/{e(f)}" alt="Foto von {e(mid)}"></a>'
                        for f in fotos) or '<p class="muted">Kein Foto im Datenblatt.</p>'
    werk_bild = next((b["datei"] for b in o.get("bilder") or [] if b.get("rolle") == "Objektfoto"), None)

    transk = "".join(f"<li>{e(x)}</li>" for x in kx["transkriptionen"])
    kommentar = (f'<h3>Kommentar der Bearbeiterin</h3><p class="quelltext">{markiert(texte["kommentar"], spans["kommentar"])}</p>'
                 if kx["kommentar"] else "")
    kette_html = "".join(
        f"<li>{z}</li>" for z in markiert(kette_text, spans["provenienzkette"]).split("<br>")) if kette_text else ""
    kette_offen = " open" if spans["provenienzkette"] else ""

    bem = by_feld.get("Bemerkungen", [])
    bem_html = "".join(schritt(t, i, "breit") for i, t in bem) or '<li class="muted">Keine.</li>'

    gruende = ", ".join(offen["llm_auswahlgrund"])
    llm = next((s for s in lauf["schichten"] if s["methode"] == "llm"), None)
    an_llm = (f"Ja — Grund: {e(gruende)}" if gruende else
              "Nein — die Regeln fanden keinen Grund (kein Kommentar, keine Transkription mit Wörtern, kein Stichwort).")
    llm_lauf = (f'<dt>LLM-Schicht</dt><dd><code>{e(llm["skript"])}</code> · <code>{e(llm["skript_sha256"])}</code> · '
                f'Modell <code>{e(llm["modell"])}</code>, effort {e(llm["effort"])}, Fotos {"ja" if llm["fotos"] else "nein"} · '
                f'Prompt <code>{e(llm["prompt_sha256"])}</code> · Namen <code>{e(llm["namen_sha256"])}</code> · {e(llm["lauf"])}</dd>'
                if llm else "")
    prev, nxt = nav
    navi = (f'<a href="{page_name(prev)}">← {e(prev)}</a>' if prev else "<span></span>") + \
           (f'<a href="{page_name(nxt)}">{e(nxt)} →</a>' if nxt else "<span></span>")

    return f"""<!DOCTYPE html>
<html lang="de-CH">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(mid)} – Herleitung</title>
<link rel="stylesheet" href="trace.css">
</head>
<body>
<header class="kopf">
  <div>
    <p class="crumb"><a href="../ui/">Provenienzmerkmale</a> › Inventar {e(inv)} › Merkmal {ds["Quellzeile"]}</p>
    <h1>{e(mid)} <span class="typ">{e(ds["Typ"] or "Typ offen")}</span></h1>
  </div>
  <nav class="navi">{navi}</nav>
</header>
<main>
<section class="karte werk">
  {f'<img class="werkbild" src="../extracted/{e(werk_bild)}" alt="">' if werk_bild else ""}
  <div>
    <p class="kuenstler">{e(o.get("kuenstler") or "")}</p>
    <p class="titel">{e(o.get("titel") or "")}{f', {e(o["datierung"])}' if o.get("datierung") else ""}</p>
    <p class="muted">Inv. {e(inv)} · {e(o.get("technik") or "")} · Status: {e(o.get("status") or "")} ·
      <a href="{e(pdf)}">Datenblatt (PDF)</a> · Stand {e(doc.get("stand") or "")}</p>
  </div>
</section>

<div class="raster">
  <section class="karte fotos"><h2>Foto</h2>{foto_html}<p class="muted">{e(kx["seite"] or "")}</p></section>
  <section class="karte quelle">
    <h2>Quelle</h2>
    <h3>Originalbeschreibung</h3>
    <p class="quelltext">{markiert(texte["beschreibung"], spans["beschreibung"])}</p>
    {kommentar}
    {f'<h3>Transkriptionen</h3><ul class="transk">{transk}</ul>' if transk else ""}
    <p class="legende"><span class="hl hl-regel">Regel</span> <span class="hl hl-verweis">Verweis</span>
      <span class="hl hl-llm">LLM</span> — Wert oder Zeile anfahren, um die Stelle zu sehen.</p>
  </section>
</div>

{tabelle("Physische Merkmale", "Von den Regeln gesetzt; das LLM darf sie nicht ändern.", PHYSISCH, ds, by_feld, prob)}
{tabelle("Deutung", "Vom LLM gesetzt, geprüft gegen Vokabular, Zitat und Quelle; Namen danach normalisiert.", DEUTUNG, ds, by_feld, prob)}

<section class="karte"><h2>Bemerkungen</h2><ul class="schritte">{bem_html}</ul></section>

<details class="karte"{kette_offen}>
  <summary><h2>Provenienzkette des Werks</h2></summary>
  <p class="hinweis">Darf Namen und Orte nur normalisieren oder verorten, nicht liefern.</p>
  <ol class="kette">{kette_html}</ol>
</details>

<section class="karte lauf">
  <h2>Herkunft dieser Werte</h2>
  <dl>
    <dt>An das LLM gesendet</dt><dd>{an_llm}</dd>
    <dt>Regel-Schicht</dt><dd><code>{e(lauf["parser"])}</code> · <code>{e(lauf["parser_sha256"])}</code> ·
      Schema <code>{e(lauf["schema"])}</code> <code>{e(lauf["schema_sha256"])}</code> · {e(lauf["lauf"])}</dd>
    {llm_lauf}
  </dl>
</section>
</main>
<script>
// hovering a highlighted passage lights up its rows, and the reverse
const on = (ids, v) => ids.split(" ").forEach(id =>
  document.querySelectorAll(`[data-t~="${{id}}"]`).forEach(n => n.classList.toggle("aktiv", v)));
document.querySelectorAll("[data-t]").forEach(n => {{
  n.addEventListener("mouseenter", () => on(n.dataset.t, true));
  n.addEventListener("mouseleave", () => on(n.dataset.t, false));
}});
</script>
</body>
</html>
"""


CSS = """:root {
  --bg: #f6f5f2; --panel: #fff; --ink: #1f2328; --muted: #646a73; --line: #dcdad4;
  --accent: #1d5c63; --accent-soft: #e2eeef;
  --regel: #1d5c63; --regel-bg: #dcebec; --verweis: #6b5b95; --verweis-bg: #e9e4f2;
  --llm: #8a5a00; --llm-bg: #fde9b8; --foto: #9a3d6b; --foto-bg: #f6dde9;
  --norm: #3f6b2f; --norm-bg: #e1eedb; --ab: #a1312b; --ab-bg: #f6dcda;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--ink); background: var(--bg);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #17191c; --panel: #202328; --ink: #e6e6e3; --muted: #9aa0a8; --line: #343840;
    --accent: #7cc4cb; --accent-soft: #23393b;
    --regel: #7cc4cb; --regel-bg: #1f3a3d; --verweis: #b9a8e0; --verweis-bg: #332c45;
    --llm: #f0c060; --llm-bg: #4a3a12; --foto: #e8a0c4; --foto-bg: #4a2537;
    --norm: #a6d18f; --norm-bg: #2a3b22; --ab: #f19c95; --ab-bg: #4a2422;
  }
}
* { box-sizing: border-box; }
body { margin: 0; font-size: 15px; line-height: 1.45; }
a { color: var(--accent); }
code { font-size: .85em; }
.muted, .hinweis { color: var(--muted); }
.hinweis { margin: -.3rem 0 .7rem; font-size: .9rem; }
.kopf { display: flex; justify-content: space-between; align-items: end; gap: 1rem; flex-wrap: wrap;
  padding: .8rem 1.25rem; background: var(--panel); border-bottom: 1px solid var(--line); }
.crumb { margin: 0; font-size: .85rem; color: var(--muted); }
h1 { margin: .1rem 0 0; font-size: 1.5rem; }
.typ { font-size: .8rem; font-weight: 600; background: var(--accent-soft); color: var(--accent);
  padding: .15rem .55rem; border-radius: 99px; vertical-align: middle; }
.navi { display: flex; gap: 1rem; }
main { max-width: 1180px; margin: 0 auto; padding: 1rem 1.25rem 3rem; display: grid; gap: 1rem; }
.karte { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: .9rem 1.1rem; }
h2 { font-size: 1rem; margin: 0 0 .6rem; }
h3 { font-size: .8rem; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); margin: .9rem 0 .3rem; }
h3:first-of-type { margin-top: 0; }
.werk { display: flex; gap: 1rem; align-items: center; }
.werk p { margin: 0; }
.werkbild { width: 84px; height: 84px; object-fit: cover; border-radius: 4px; }
.kuenstler { font-weight: 600; }
.titel { font-style: italic; }
.raster { display: grid; grid-template-columns: minmax(240px, 360px) 1fr; gap: 1rem; }
@media (max-width: 760px) { .raster { grid-template-columns: 1fr; } }
.fotos img { width: 100%; border-radius: 4px; display: block; margin-bottom: .5rem; }
.quelltext { font-size: 1.05rem; margin: 0; }
.transk { margin: 0; padding-left: 1.1rem; }
.legende { margin: 1rem 0 0; font-size: .85rem; color: var(--muted); }
.hl { border-radius: 2px; padding: 0 1px; transition: outline .1s; }
.hl-regel { box-shadow: inset 0 -2px var(--regel); background: var(--regel-bg); }
.hl-verweis { box-shadow: inset 0 -2px var(--verweis); background: var(--verweis-bg); }
.hl-llm { background: var(--llm-bg); }
.hl-llm.hl-regel { background: linear-gradient(var(--llm-bg), var(--llm-bg)); }
.hl.aktiv, .schritt.aktiv { outline: 2px solid var(--accent); outline-offset: 1px; }
.werte { width: 100%; border-collapse: collapse; }
.werte th, .werte td { text-align: left; vertical-align: top; padding: .45rem .5rem; border-top: 1px solid var(--line); }
.werte thead th { font-size: .8rem; color: var(--muted); font-weight: 600; border-top: 0; }
.werte th[scope=row] { width: 11rem; font-weight: 600; font-size: .9rem; }
.werte td.final { width: 15rem; }
.werte tr.leer th, .werte tr.leer td { color: var(--muted); font-weight: 400; }
.chip { display: inline-block; background: var(--accent-soft); border-radius: 4px; padding: 0 .4rem; margin: 0 .25rem .2rem 0; }
.schritte { list-style: none; margin: 0; padding: 0; display: grid; gap: .3rem; }
.schritt { display: grid; grid-template-columns: 7.5rem minmax(6rem, auto) 1fr; gap: .1rem .6rem; align-items: baseline;
  font-size: .9rem; border-radius: 4px; padding: .1rem .2rem; }
.schritt .warum { grid-column: 2 / -1; color: var(--muted); font-size: .85rem; }
.schritt .wert { font-weight: 600; }
.zitat { font-style: normal; } .zitat::before { content: "«"; } .zitat::after { content: "»"; }
.zitat.roh::before, .zitat.roh::after { content: none; }
.schritt.breit { grid-template-columns: 7.5rem 1fr; }
.schritt.breit .beleg, .schritt.breit .warum { grid-column: 2; }
.wo { color: var(--muted); font-size: .8rem; margin-right: .3rem; }
.von { text-decoration: line-through; color: var(--muted); }
.m { font-size: .7rem; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; text-align: center;
  border-radius: 99px; padding: .1rem .4rem; }
.m-regel { color: var(--regel); background: var(--regel-bg); }
.m-verweis { color: var(--verweis); background: var(--verweis-bg); }
.m-llm { color: var(--llm); background: var(--llm-bg); }
.m-foto { color: var(--foto); background: var(--foto-bg); }
.m-normalisierung { color: var(--norm); background: var(--norm-bg); }
.m-abgelehnt { color: var(--ab); background: var(--ab-bg); }
.abgelehnt .wert { text-decoration: line-through; }
details summary { cursor: pointer; list-style: none; }
details summary h2 { display: inline; }
details summary::before { content: "▸ "; color: var(--muted); }
details[open] summary::before { content: "▾ "; }
.kette { margin: 0; padding-left: 1.3rem; font-size: .9rem; }
.lauf dl { display: grid; grid-template-columns: 11rem 1fr; gap: .3rem .8rem; margin: 0; font-size: .9rem; }
.lauf dt { color: var(--muted); }
.lauf dd { margin: 0; }
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("parsed", type=Path)
    ap.add_argument("extracted", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--ids", nargs="*")
    a = ap.parse_args()

    data = json.loads((a.parsed / "merkmale.json").read_text())
    recs = data["merkmale"]
    probleme: dict[str, list] = {}
    tsv = a.parsed / "llm_probleme.tsv"
    if tsv.exists():
        with tsv.open() as f:
            for row in csv.DictReader(f, delimiter="\t"):
                if row["Art"] == "abgelehnt":
                    probleme.setdefault(row["Merkmal-ID"], []).append(row)

    quellorte(Path(__file__).resolve().parent.parent,
              {t["regel"] for r in recs for t in r["trace"] if t.get("regel")})
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "trace.css").write_text(CSS)
    docs: dict[str, dict] = {}
    wanted = set(a.ids) if a.ids else None
    for n, rec in enumerate(recs):
        mid = rec["datensatz"]["Merkmal-ID"]
        if wanted and mid not in wanted:
            continue
        inv = rec["datensatz"]["Inventarnummer"]
        if inv not in docs:
            docs[inv] = json.loads((a.extracted / "json" / f"{inv}.json").read_text())
        same = lambda r: r["datensatz"]["Inventarnummer"] == inv
        prev = recs[n - 1]["datensatz"]["Merkmal-ID"] if n > 0 and same(recs[n - 1]) else None
        nxt = recs[n + 1]["datensatz"]["Merkmal-ID"] if n + 1 < len(recs) and same(recs[n + 1]) else None
        page = render(rec, docs[inv], data["lauf"], probleme.get(mid, []), (prev, nxt))
        (a.out / page_name(mid)).write_text(page)
    print(f"wrote {len(wanted) if wanted else len(recs)} pages to {a.out}")


if __name__ == "__main__":
    main()
