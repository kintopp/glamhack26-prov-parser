# /// script
# requires-python = ">=3.11"
# dependencies = ["pymupdf>=1.24"]
# ///
"""Check extracted JSON against the PDFs: missing fields, footnotes, images, lost words.

Usage: uv run scripts/validate.py PDF_DIR OUT_DIR
"""
import collections
import json
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).parent))
import extract_datenblatt as E  # noqa: E402

# Template labels that the extractor turns into keys rather than values
TEMPLATE = set("""Datum Institution Archiv Bestand Betreff Antwort Zeitpunkt Zeitraum Besitzer in Eigentümer
Standort Datenbanken Erklärung Inventarnummer Status Bearbeitet Stand""".split())


def words(s: str) -> list[str]:
    return re.findall(r"[^\W_]{2,}", s)


def strings(o, skip=("datei", "bbox", "fussnoten")):
    if isinstance(o, dict):
        for k, v in o.items():
            if k not in skip:
                yield from strings(v, skip)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v, skip)
    elif isinstance(o, str):
        yield o


def main(src: Path, out: Path):
    problems = collections.defaultdict(list)
    for pdf in sorted(src.glob("*.pdf")):
        inv = re.match(r"Datenblatt_Provenienzforschung_(.+)_\d{6,8}$", pdf.stem).group(1)
        rec = json.loads((out / "json" / f"{inv}.json").read_text())
        o = rec["objekt"]
        for k in ("inventarnummer", "kuenstler", "titel", "datierung", "technik", "status", "eigentuemer"):
            if not o.get(k):
                problems[f"objekt.{k} leer"].append(inv)
        if not o.get("masse"):
            problems["objekt.masse leer"].append(inv)
        if o.get("inventarnummer") and o["inventarnummer"] != inv:
            problems["Inventarnummer ≠ Dateiname"].append(f"{inv} vs {o['inventarnummer']}")
        if not o.get("bilder"):
            problems["kein Objektfoto"].append(inv)
        if not rec["provenienz"]:
            problems["keine Provenienz"].append(inv)
        if not rec["provenienzmerkmale"]["merkmale"]:
            problems["keine Merkmale"].append(inv)
        if not rec["provenienzmerkmale"]["erklaerung"]:
            problems["keine Erklärung"].append(inv)
        for m in rec["provenienzmerkmale"]["merkmale"]:
            if not m["beschreibung"]:
                problems["Merkmal ohne Beschreibung"].append(f"{inv}#{m['nr']}")
            if not m["bilder"]:
                problems["Merkmal ohne Bild"].append(f"{inv}#{m['nr']}")

        nums = [f["nr"] for f in rec["fussnoten"]]
        if nums != list(range(1, len(nums) + 1)):
            problems["Fussnoten nicht 1..N"].append(f"{inv}: {nums}")
        used = set()

        def walk(x):
            if isinstance(x, dict):
                for f in x.get("fussnoten", []) if x is not rec else []:
                    used.add(f["nr"])
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(rec)
        if set(nums) - used:
            problems["Fussnote nie referenziert"].append(f"{inv}: {sorted(set(nums) - used)}")
        if used - set(nums):
            problems["Referenz ohne Fussnote"].append(f"{inv}: {sorted(used - set(nums))}")

        # images and text coverage against the PDF
        doc = pymupdf.open(pdf)
        pages = [E.load_page(p) for p in doc]
        all_x = {i.tiles for p in pages for i in p.images}
        placed = len({b["datei"] for b in strings_imgs(rec)})
        if placed != len(all_x):
            problems["Bilder nicht zugeordnet"].append(f"{inv}: {placed}/{len(all_x)}")
        body = collections.Counter()
        for p in pages:
            for l in p.lines:
                if l.y0 < p.fn_top and l.y1 > E.HEADER_BOTTOM and not E.is_footer(l):
                    body.update(w for w in words(E.split_marks(l.text())[0]) if w not in TEMPLATE)
        got = collections.Counter(w for s in strings(rec) for w in words(s))
        # words broken by hyphenation appear as halves in the PDF; ignore halves found inside joined words
        joined = " ".join(strings(rec))
        lost = [w for w, n in (body - got).items() if w not in joined and w.upper() != w]
        if lost:
            problems["Wörter fehlen im JSON"].append(f"{inv}: {lost[:12]}")
    for k, v in problems.items():
        print(f"\n## {k} ({len(v)})")
        for x in v[:15]:
            print("  ", x)


def strings_imgs(o):
    if isinstance(o, dict):
        if "datei" in o and str(o["datei"]).startswith("images/"):
            yield o
        for v in o.values():
            yield from strings_imgs(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings_imgs(v)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
