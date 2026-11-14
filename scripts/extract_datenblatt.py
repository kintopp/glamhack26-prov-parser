# /// script
# requires-python = ">=3.11"
# dependencies = ["pymupdf>=1.24", "openpyxl>=3.1"]
# ///
"""Extract fields and images from SKKG "Datenblatt Provenienzforschung" PDFs.

Usage: uv run scripts/extract_datenblatt.py PDF_DIR OUT_DIR

The sheets are Word exports with ruled tables. Rows and columns are taken from
the grey border rules, not from text heuristics; characters and images are then
placed into (row, column) cells.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

SECTIONS = [
    "OBJEKT", "PROVENIENZ", "RECHERCHE", "ANFRAGEN", "ARCHIV INTERN",
    "ARCHIVE EXTERN", "KONTEXTINFORMATIONEN", "PROVENIENZMERKMALE",
]
HEADER_BOTTOM = 115      # logo zone
FOOTER_TOP = 770         # "Seite n/m"
SUP_MAX_SIZE = 8.5       # body 11pt / footnotes 9pt; reference marks are 6-7pt
DATE_LIKE = re.compile(
    r"(o\.\s?J\.|o\.\s?D\.|um\b|ca\.|vor\b|nach\b|zwischen\b|wohl\b|vermutlich\b|frühestens\b|"
    r"spätestens\b|\d{3,4}\b|\d{1,2}\.\s?Jh|(Anfang|Mitte|Ende|[Ff]rüh|[Ss]pät)\w*\b)")
MEASURE = re.compile(r"\d\s*[×x]\s*\d|\bcm\b|Ø")
MONTHS = {m: i + 1 for i, m in enumerate(
    "Januar Februar März April Mai Juni Juli August September Oktober November Dezember".split())}

# Word's Symbol-font characters arrive as private-use code points
SYMBOL_PUA = {"\uf02d": "–"}


# ---------------------------------------------------------------- page model

@dataclass
class Ch:
    c: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool
    italic: bool
    page: int = 0

    @property
    def sup(self) -> bool:
        return self.size < SUP_MAX_SIZE


@dataclass
class Line:
    chars: list[Ch]

    @property
    def x0(self): return min(c.x0 for c in self.chars)
    @property
    def page(self): return self.chars[0].page
    @property
    def y0(self): return min(c.y0 for c in self.chars if not c.sup) if self.main else min(c.y0 for c in self.chars)
    @property
    def y1(self): return max(c.y1 for c in self.chars)
    @property
    def main(self): return [c for c in self.chars if not c.sup]
    @property
    def size(self): return max((c.size for c in self.main), default=0)
    @property
    def bold(self): return self._share("bold") > 0.6
    @property
    def italic(self): return self._share("italic") > 0.6

    def _share(self, attr) -> float:
        letters = [c for c in self.main if c.c.isalpha()]
        return sum(getattr(c, attr) for c in letters) / len(letters) if letters else 0.0

    def text(self, marks=True) -> str:
        """Line text; superscript digit runs become ⟦n⟧ when marks is True."""
        out, prev = [], None
        chars = sorted(self.chars, key=lambda c: c.x0)
        i = 0
        while i < len(chars):
            c = chars[i]
            if prev is not None:
                gap = c.x0 - prev.x1
                em = max(prev.size, c.size)
                if gap > 0.22 * em and not prev.c.isspace() and not c.c.isspace() and not c.sup:
                    out.append(" ")
            if c.sup and self.main:
                j = i
                while j < len(chars) and chars[j].sup:
                    j += 1
                run = "".join(x.c for x in chars[i:j])
                if marks and re.fullmatch(r"[\d,\s]+", run) and run.strip():
                    out.append("".join(f"⟦{n}⟧" for n in re.findall(r"\d+", run)))
                else:
                    out.append(run)
                prev = chars[j - 1]
                i = j
                continue
            out.append(c.c)
            prev = c
            i += 1
        return "".join(out)


@dataclass
class Img:
    xref: int
    bbox: tuple
    page: int
    width: int = 0
    tiles: tuple = ()          # xrefs of horizontal strips that together form this image


@dataclass
class Page:
    number: int
    lines: list[Line]
    images: list[Img]
    hrules: list[tuple]        # (y, x0, x1) grey table rules
    vrules: list[tuple]        # (x, y0, y1)
    fn_top: float              # y of footnote separator, or FOOTER_TOP


def load_page(page: pymupdf.Page) -> Page:
    hr, vr, black = [], [], []
    for d in page.get_drawings():
        r = d["rect"]
        col = d.get("fill") or d.get("color") or (0, 0, 0)
        dark = max(col) < 0.3
        if r.height < 1.5 and r.width > 3:
            (black if dark else hr).append((r.y0, r.x0, r.x1))
        elif r.width < 1.5 and r.height > 3 and not dark:
            vr.append((r.x0, r.y0, r.y1))
    # footnote separator: short rule, or full width when notes continue from the previous page
    seps = [y for y, x0, x1 in black if x0 < 60 and (120 < x1 - x0 < 170 or x1 - x0 > 470)]
    fn_top = min(seps) if seps else FOOTER_TOP

    lines = []
    raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP)
    for b in raw["blocks"]:
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            chars = []
            for s in l["spans"]:
                bold = "Bold" in s["font"] or bool(s["flags"] & 16)
                italic = "Italic" in s["font"] or bool(s["flags"] & 2)
                for c in s["chars"]:
                    x0, y0, x1, y1 = c["bbox"]
                    ch = SYMBOL_PUA.get(c["c"], c["c"])
                    chars.append(Ch(ch, x0, y0, x1, y1, s["size"], bold, italic, page.number + 1))
            if any(not c.c.isspace() for c in chars):
                lines.append(Line(chars))

    imgs: list[Img] = []
    infos = [i for i in page.get_image_info(xrefs=True) if i["bbox"][3] >= HEADER_BOTTOM and i["xref"]]
    for info in sorted(infos, key=lambda i: i["bbox"][1]):
        x0, y0, x1, y1 = info["bbox"]
        last = imgs[-1] if imgs else None
        # some producers slice a large picture into abutting strips
        if (last and last.width == info["width"] and abs(last.bbox[0] - x0) < 1 and abs(last.bbox[2] - x1) < 1
                and abs(last.bbox[3] - y0) < 1.5):
            last.bbox = (last.bbox[0], last.bbox[1], last.bbox[2], y1)
            last.tiles += (info["xref"],)
            continue
        imgs.append(Img(info["xref"], (x0, y0, x1, y1), page.number + 1, info["width"], (info["xref"],)))
    imgs = [i for i in imgs if (i.bbox[2] - i.bbox[0]) >= 3 and (i.bbox[3] - i.bbox[1]) >= 3]
    return Page(page.number + 1, lines, imgs, hr, vr, fn_top)


def cluster(values, tol):
    out = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


# ---------------------------------------------------------------- text helpers

def join_lines(lines: list[str]) -> str:
    """Join wrapped lines, undoing Word's hyphenation."""
    out = ""
    for t in lines:
        t = t.strip()
        if not out:
            out = t
            continue
        last_word = out.split()[-1] if out.split() else ""
        url = "http" in last_word or "/" in last_word or "www." in last_word
        hyphen = out.endswith("-") and len(out) > 1 and out[-2].isalpha() and not url
        if hyphen and t.split(" ", 1)[0] in ("und", "oder", "bzw.", "sowie"):
            out += " " + t                      # suspended compound: "Kunst- und"
        elif hyphen and t[:1].islower():
            out = out[:-1] + t                  # Word hyphenation
        elif hyphen and t[:1].isupper():
            out += t                            # real hyphen: "Haegler-Passavant"
        elif url and (out.endswith("-") or out.endswith("/") or out.endswith(".")) and not t[:1].isupper():
            out += t
        elif out.endswith("/") or (out.endswith("–") and out[-2:-1].isdigit() and t[:1].isdigit()):
            out += t
        else:
            out += " " + t
    return out


def split_marks(text: str) -> tuple[str, list[int]]:
    refs = [int(n) for n in re.findall(r"⟦(\d+)⟧", text)]
    clean = re.sub(r"\s*⟦\d+⟧", "", text)
    clean = re.sub(r"[ \t]+", " ", clean).strip()
    return clean, refs


def iso_date(s: str) -> str | None:
    m = re.search(r"(\d{1,2})\.\s*([A-Za-zä]+)\s+(\d{4})", s or "")
    if m and m.group(2) in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"
    return None


# ---------------------------------------------------------------- cells

@dataclass
class Cell:
    lines: list[Line] = field(default_factory=list)
    images: list[Img] = field(default_factory=list)

    def sorted_lines(self) -> list[Line]:
        """Merge fragments on the same visual line, order top to bottom."""
        frags = sorted(self.lines, key=lambda l: (l.page, l.y0, l.x0))
        merged: list[Line] = []
        for f in frags:
            if merged and f.page == merged[-1].page and (abs(f.y0 - merged[-1].y0) < 3 or not f.main):
                merged[-1] = Line(merged[-1].chars + f.chars)
            else:
                merged.append(f)
        return merged

    def blocks(self) -> list[dict]:
        """Paragraphs and images in reading order. Paragraph break = vertical gap."""
        items = [("line", (l.page, l.y0), l) for l in self.sorted_lines()] + \
                [("img", (i.page, i.bbox[1]), i) for i in self.images]
        items.sort(key=lambda t: t[1])
        out, cur, prev_top, prev_page = [], [], None, None
        for kind, (pg, y), obj in items:
            page_break = prev_page is not None and pg != prev_page
            prev_page = pg
            if kind == "img":
                if cur:
                    out.append({"lines": cur})
                    cur = []
                out.append({"image": obj})
                prev_top = None
                continue
            if page_break:
                prev_end = cur[-1].text(False).rstrip() if cur else ""
                runs_on = prev_end.endswith(("-", ",")) or obj.text(False).lstrip()[:1].islower()
                pitch_break = not runs_on
            else:
                pitch_break = prev_top is not None and obj.y0 - prev_top > 1.45 * max(obj.size, 9)
            if cur and pitch_break:
                out.append({"lines": cur})
                cur = []
            cur.append(obj)
            prev_top = obj.y0
        if cur:
            out.append({"lines": cur})
        return out

    @property
    def empty(self) -> bool:
        return not self.images and not any(l.text(False).strip() for l in self.lines)


def page_rows(pg: Page, y0: float, y1: float) -> list[list[Cell]]:
    """Grid of cells for the table area of a page between y0 and y1."""
    ys = cluster([y for y, a, b in pg.hrules if y0 - 2 <= y <= y1 + 2], 2.0)
    xs = cluster([x for x, a, b in pg.vrules if min(b, y1) - max(a, y0) > 3], 6.0)
    if len(xs) < 2:
        xs = [56.0, 540.0]
    bounds_y = sorted(set([y0] + ys + [y1]))
    cols = [(a, b) for a, b in zip(xs, xs[1:]) if b - a > 8]

    def col_of(x):
        for i, (a, b) in enumerate(cols):
            if a - 1 <= x < b:
                return i
        return 0 if x < cols[0][0] else len(cols) - 1

    def row_of(y):
        for i, (a, b) in enumerate(zip(bounds_y, bounds_y[1:])):
            if a <= y < b:
                return i
        return None

    # A column boundary exists in a row only where a rule crosses it; otherwise
    # the cells are merged and their content belongs to the leftmost one.
    def boundary_in_row(i, ra, rb):
        return any(abs(x - cols[i][0]) < 4 and min(vb, rb) - max(va, ra) > 0.5 * (rb - ra)
                   for x, va, vb in pg.vrules)

    spans = []
    for ra, rb in zip(bounds_y, bounds_y[1:]):
        eff, cur = [], 0
        for i in range(len(cols)):
            if i and boundary_in_row(i, ra, rb):
                cur = i
            eff.append(cur)
        spans.append(eff)

    grid = [[Cell() for _ in cols] for _ in bounds_y[1:]]
    for line in pg.lines:
        ly = (line.y0 + line.y1) / 2
        if not (y0 <= ly < y1):
            continue
        r = row_of(ly)
        if r is None:
            continue
        parts: dict[int, list[Ch]] = {}
        for c in line.chars:
            parts.setdefault(spans[r][col_of((c.x0 + c.x1) / 2)], []).append(c)
        for ci, chars in parts.items():
            if any(not c.c.isspace() for c in chars):
                grid[r][ci].lines.append(Line(chars))
    for img in pg.images:
        cy = (img.bbox[1] + img.bbox[3]) / 2
        if not (y0 <= cy < y1):
            continue
        r = row_of(cy)
        if r is not None:
            grid[r][spans[r][col_of(img.bbox[0] + 4)]].images.append(img)
    return [row for row in grid if not all(c.empty for c in row)]


# ---------------------------------------------------------------- document

@dataclass
class Region:
    section: str
    page: Page
    y0: float
    y1: float
    continued: bool   # section started on an earlier page


def is_footer(line: Line) -> bool:
    return bool(re.fullmatch(r"\s*Seite\s+\d+\s*/\s*\d+\s*", line.text(False)))


def regions(pages: list[Page]) -> list[Region]:
    out, current, idx = [], "KOPF", 0
    for pg in pages:
        bottom = min(pg.fn_top, FOOTER_TOP)
        heads = []
        for l in pg.lines:
            t = l.text(False).strip()
            if idx < len(SECTIONS) and t in SECTIONS[idx:] and l.x0 < 70 and l.size >= 10.5:
                # sections are in fixed order; take the next expected one
                if SECTIONS.index(t) >= idx:
                    heads.append((l.y0, l.y1, t))
                    idx = SECTIONS.index(t) + 1
        heads.sort()
        top, cont = HEADER_BOTTOM, True
        for hy0, hy1, name in heads:
            out.append(Region(current, pg, top, hy0 - 1, cont))
            current, top, cont = name, hy1 + 1, False
        out.append(Region(current, pg, top, bottom, cont))
    return out


def is_divider(row: list[Cell]) -> bool:
    texts = [" ".join(l.text(False).strip() for l in c.lines).strip() for c in row]
    return [t for t in texts if t] == ["Datenbanken"]


def continues_first_col(row: list[Cell]) -> bool:
    return row[0].empty


def continues_mark(row: list[Cell]) -> bool:
    # a new mark has both a photo and a description
    return row[0].empty and (row[1].empty or all(c.empty for c in row[2:]))


def fold(row: list[Cell], ncols: int | None) -> list[Cell]:
    """Pad or fold a row to ncols cells; surplus columns go into the last cell."""
    if not ncols:
        return row
    row = row + [Cell() for _ in range(ncols - len(row))]
    tail = row[ncols - 1:]
    return row[:ncols - 1] + [Cell([l for c in tail for l in c.lines], [i for c in tail for i in c.images])]


def section_rows(regs: list[Region], name: str, continues=continues_first_col,
                 ncols: int | None = None) -> list[list[Cell]]:
    """All rows of a section across pages, merging rows split by a page break."""
    rows: list[list[Cell]] = []
    for reg in [r for r in regs if r.section == name]:
        prow = page_rows(reg.page, reg.y0, reg.y1)
        for i, row in enumerate(prow):
            row = fold(row, ncols)
            split = (reg.continued and i == 0 and rows and len(row) == len(rows[-1])
                     and continues(row) and not is_divider(row))
            if split:
                for a, b in zip(rows[-1], row):
                    a.lines += b.lines
                    a.images += b.images
            else:
                rows.append(row)
    return rows


class Doc:
    def __init__(self, path: Path, img_dir: Path):
        self.path = path
        self.pdf = pymupdf.open(path)
        self.pages = [load_page(p) for p in self.pdf]
        self.regs = regions(self.pages)
        self.footnotes = self.read_footnotes()
        self.img_dir = img_dir
        self.saved: dict[tuple, str] = {}
        self.images: list[dict] = []
        self.inv = None

    # footnotes ---------------------------------------------------------
    def read_footnotes(self) -> dict[int, str]:
        notes: dict[int, list[str]] = {}
        last = None
        for pg in self.pages:
            fl = [l for l in pg.lines if l.y0 > pg.fn_top and l.y1 < FOOTER_TOP + 10 and not is_footer(l)]
            for l in sorted(fl, key=lambda l: (round(l.y0), l.x0)):
                chars = sorted(l.chars, key=lambda c: c.x0)
                lead = ""
                for c in chars:
                    if c.sup and (c.c.isdigit()):
                        lead += c.c
                    elif c.c.isspace() and not lead:
                        continue
                    else:
                        break
                if lead:
                    last = int(lead)
                    rest = Line([c for c in chars if not (c.sup and c.x0 <= chars[0].x0 + 8)])
                    notes[last] = [rest.text(False)]
                elif last is not None:
                    notes[last].append(l.text(False))
        return {n: join_lines(v) for n, v in notes.items()}

    def fn(self, refs: list[int]) -> list[dict]:
        return [{"nr": n, "text": self.footnotes.get(n)} for n in dict.fromkeys(refs)]

    # images --------------------------------------------------------------
    def image(self, img: Img, section: str, role: str, caption: str | None = None, label: str | None = None) -> dict:
        if img.tiles not in self.saved:
            if len(img.tiles) == 1:
                data = self.pdf.extract_image(img.xref)
                blob, ext = data["image"], data["ext"]
            else:
                blob, ext = self.stitch(img.tiles), "png"
            n = len(self.saved) + 1
            name = f"{self.inv}_{n:03d}_{section.lower().replace(' ', '_')}.{ext}"
            (self.img_dir / name).write_bytes(blob)
            self.saved[img.tiles] = name
        rec = {
            "datei": f"images/{self.img_dir.name}/{self.saved[img.tiles]}",
            "seite": img.page,
            "bbox": [round(v, 1) for v in img.bbox],
            "abschnitt": section,
            "rolle": role,
        }
        if label:
            rec["bildtitel"] = label
        if caption:
            rec["bildunterschrift"] = caption
        self.images.append({"inventarnummer": self.inv, **rec})
        return rec

    def stitch(self, xrefs: tuple) -> bytes:
        strips = []
        for x in xrefs:
            pix = pymupdf.Pixmap(self.pdf, x)
            if pix.alpha:
                pix = pymupdf.Pixmap(pix, 0)
            if pix.colorspace is None or pix.colorspace.n != 3:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            strips.append(pix)
        out = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, max(p.width for p in strips),
                                                           sum(p.height for p in strips)), False)
        y = 0
        for pix in strips:
            pix.set_origin(0, y)
            out.copy(pix, pix.irect)
            y += pix.height
        return out.tobytes("png")

    def cell_content(self, cell: Cell, section: str, role: str) -> dict:
        """Text, footnotes and images of a cell.

        A paragraph ending in ':' right before an image is that image's label;
        a paragraph right below an image is its caption.
        """
        paras, refs, imgs = [], [], []
        blocks = cell.blocks()
        for i, b in enumerate(blocks):
            if "image" in b:
                continue
            t, r = split_marks(join_lines([l.text() for l in b["lines"]]))
            if not t:
                continue
            refs += r
            prev = blocks[i - 1] if i else None
            nxt = blocks[i + 1] if i + 1 < len(blocks) else None
            first, last = b["lines"][0], b["lines"][-1]
            if (t.endswith(":") and nxt and "image" in nxt and nxt["image"].page == last.page
                    and nxt["image"].bbox[1] - last.y1 < 25):
                nxt["label"] = t.rstrip(":").strip()
            elif (prev and "image" in prev and "caption" not in prev and first.page == prev["image"].page
                    and first.y0 - prev["image"].bbox[3] < 14):
                prev["caption"] = t
            else:
                paras.append(t)
        for b in blocks:
            if "image" in b:
                imgs.append(self.image(b["image"], section, role, b.get("caption"), b.get("label")))
        out = {"text": "\n\n".join(paras)}
        if imgs:
            out["bilder"] = imgs
        if refs:
            out["fussnoten"] = self.fn(refs)
        return out

    # sections ------------------------------------------------------------
    def kopf(self) -> dict:
        lines = [l for r in self.regs if r.section == "KOPF" for l in r.page.lines
                 if r.y0 <= l.y0 < r.y1 and not is_footer(l)]
        texts = [l.text(False).strip() for l in sorted(lines, key=lambda l: l.y0)]
        out = {"titel": next((t for t in texts if t.startswith("Dokumentation")), None)}
        for t in texts:
            if t.startswith("Bearbeitet von:"):
                out["bearbeitet_von"] = t.split(":", 1)[1].strip()
            elif t.startswith("Stand:"):
                out["stand"] = t.split(":", 1)[1].strip()
                out["stand_iso"] = iso_date(out["stand"])
        return out

    def objekt(self) -> dict:
        cells = [c for row in section_rows(self.regs, "OBJEKT") for c in row]
        txt_cell = next((c for c in cells if any(l.text(False).strip().startswith("Inventarnummer")
                                                 for l in c.lines)), None)
        out: dict = {}
        refs: list[int] = []
        logical = []  # (text, bold, italic) per paragraph, wrapped lines joined
        for blk in (txt_cell.blocks() if txt_cell else []):
            if "lines" not in blk:
                continue
            para = []
            for l in blk["lines"]:
                t = l.text().strip()
                # one field per line here; only a trailing hyphen/comma/slash continues one
                if para and para[-1][0].endswith(("-", ",", "/")):
                    para[-1][0] = join_lines([para[-1][0], t])
                else:
                    para.append([t, l.bold, l.italic])
            logical.append(para)

        def clean(t):
            c, r = split_marks(t)
            refs.extend(r)
            return c

        out["zeilen"] = [clean(t) for p in logical for t, _, _ in p]
        body = []
        for p in logical:
            keep = []
            for t, b, i in p:
                if t.startswith("Inventarnummer:"):
                    out["inventarnummer"] = split_marks(t.split(":", 1)[1])[0]
                elif t.startswith("Status:"):
                    out["status"] = split_marks(t.split(":", 1)[1])[0]
                else:
                    keep.append((split_marks(t)[0], b, i))
            if keep:
                body.append(keep)
        work = body[0] if body else []
        i = 0
        artists = []
        while i < len(work) and work[i][1]:
            artists.append(work[i][0])
            i += 1
        if not artists and work and not work[0][2] and not DATE_LIKE.match(work[0][0]):
            artists.append(work[0][0])
            i = 1
        title = []
        while i < len(work) and work[i][2]:
            title.append(work[i][0])
            i += 1
        if not title:  # upright descriptive title: everything up to the date line
            j = next((k for k in range(i, len(work)) if DATE_LIKE.match(work[k][0])), None)
            if j is not None and j > i:
                title, i = [t for t, _, _ in work[i:j]], j
        out["kuenstler"] = " / ".join(artists) or None
        out["titel"] = " ".join(title) or None
        rest = [t for t, _, _ in work[i:]]
        out["masse"] = [t for t in rest if MEASURE.search(t)]
        other = [t for t in rest if t not in out["masse"]]
        out["datierung"] = next((t for t in other if DATE_LIKE.match(t)), None)
        if out["datierung"]:
            other.remove(out["datierung"])
        out["technik"] = other.pop(0) if other else None
        out["bezeichnung"] = "; ".join(other) or None
        out["eigentuemer"] = " ".join(t for p in body[1:] for t, _, _ in p) or None

        imgs = []
        for c in cells:
            if c is not txt_cell and (c.images or c.lines):
                got = self.cell_content(c, "OBJEKT", "Objektfoto")
                imgs += got.get("bilder", [])
                refs += [f["nr"] for f in got.get("fussnoten", [])]
                if got["text"]:
                    out["bildtext"] = got["text"]
        if imgs:
            out["bilder"] = imgs
        if refs:
            out["fussnoten"] = self.fn(refs)
        return out

    def merge(self, keys, contents) -> dict:
        rec, fns, imgs = {}, [], []
        for k, c in zip(keys, contents):
            rec[k] = c["text"]
            fns += c.get("fussnoten", [])
            imgs += c.get("bilder", [])
        if imgs:
            rec["bilder"] = imgs
        if fns:
            rec["fussnoten"] = fns
        return rec

    def table(self, name: str, header: list[str], keys: list[str]) -> list[dict]:
        out = []
        for row in section_rows(self.regs, name, ncols=len(keys)):
            texts = [" ".join(l.text(False).strip() for l in c.sorted_lines()) for c in row]
            if [t for t in texts if t] == header[:len([t for t in texts if t])] and texts[0] == header[0]:
                continue
            rec = self.merge(keys, [self.cell_content(c, name, k) for c, k in zip(row, keys)])
            if all(rec[k] in ("", "–", "-") for k in keys) and "bilder" not in rec:
                continue
            out.append(rec)
        return out

    def provenienz(self) -> list[dict]:
        return self.table("PROVENIENZ", ["Zeitpunkt/Zeitraum", "Besitzer:in/Eigentümer:in/Standort"],
                          ["zeitraum", "besitzer"])

    def recherche(self) -> dict:
        lit, dbs, in_db = [], [], False
        for row in section_rows(self.regs, "RECHERCHE", ncols=2):
            if is_divider(row):
                in_db = True
                continue
            label, label_refs = split_marks(join_lines([l.text() for l in row[0].sorted_lines()]))
            val = self.cell_content(Cell([l for c in row[1:] for l in c.lines], [i for c in row[1:] for i in c.images]),
                                    "RECHERCHE", label or "Recherche")
            if label_refs:
                val["fussnoten"] = self.fn(label_refs) + val.get("fussnoten", [])
            if not in_db:
                rec = {"kategorie": label, "text": val["text"]}
                rec.update({k: v for k, v in val.items() if k != "text"})
                lit.append(rec)
            else:
                searches = []
                for p in [p for p in val["text"].split("\n\n") if p and p != "–"]:
                    s = {"text": p}
                    m = re.match(r"(\d+|[Kk]ein\w*)\s+(möglich\w*\s+)?Treff", p)
                    if m:
                        s["treffer"] = int(m.group(1)) if m.group(1).isdigit() else 0
                    m = re.search(r"([\d’' ]*\d)\s+Ergebnis", p.split("[")[0])
                    if m:
                        s["ergebnisse"] = int(re.sub(r"\D", "", m.group(1)))
                    d = re.findall(r"\[(\d{1,2}\.\s*\w+\s+\d{4})\]", p)
                    if d:
                        s["datum"] = iso_date(d[-1])
                    searches.append(s)
                rec = {"datenbank": label, "suchen": searches}
                rec.update({k: v for k, v in val.items() if k not in ("text",)})
                dbs.append(rec)
        return {"literatur": lit, "datenbanken": dbs}

    def merkmale(self) -> dict:
        rows = section_rows(self.regs, "PROVENIENZMERKMALE", continues=continues_mark, ncols=3)
        out = {"erklaerung": None, "uebersicht": [], "merkmale": [], "ohne_merkmale": []}
        side = None
        for row in rows:
            side_cell, img_cell = row[0], row[1]
            desc_cell = Cell([l for c in row[2:] for l in c.lines], [i for c in row[2:] for i in c.images])
            all_lines = [l for c in row for l in c.lines]
            if any(l.text(False).lstrip().startswith("Erklärung") for l in all_lines):
                # overview row: photo(s) left, explanation right. Cells may be merged
                # or shifted, so split at the explanation's left edge.
                imgs = [i for c in row for i in c.images]
                split_x = min(l.x0 for l in all_lines if l.text(False).lstrip().startswith("Erklärung")) - 2
                right = [l for l in all_lines if l.x0 >= split_x]
                left = [l for l in all_lines if l.x0 < split_x]
                expl = self.cell_content(Cell(right), "PROVENIENZMERKMALE", "Erklärung")
                over = self.cell_content(Cell(left, imgs), "PROVENIENZMERKMALE", "Übersicht")
                out["erklaerung"] = re.sub(r"^Erklärung:\s*", "", expl["text"])
                out["uebersicht"] = over.get("bilder", [])
                if over["text"]:
                    out["uebersicht_text"] = over["text"]
                fns = over.get("fussnoten", []) + expl.get("fussnoten", [])
                if fns:
                    out["fussnoten"] = fns
                continue
            desc = self.cell_content(desc_cell, "PROVENIENZMERKMALE", "Merkmal")
            if side_cell.images:  # side column merged with the image column in this row
                img_cell = Cell(side_cell.lines + img_cell.lines, side_cell.images + img_cell.images)
                side_cell = Cell()
            lab, side_refs = split_marks(join_lines([l.text() for l in side_cell.sorted_lines()]))
            if lab:
                side = lab
            pics = self.cell_content(img_cell, "PROVENIENZMERKMALE", "Merkmal")
            fns = self.fn(side_refs) + pics.get("fussnoten", []) + desc.get("fussnoten", [])
            if desc["text"] in ("", "–") and pics["text"] in ("", "–") and not pics.get("bilder"):
                if desc["text"] == "–" and side:  # placeholder: no marks on this side
                    out["ohne_merkmale"].append(side)
                if fns:
                    out.setdefault("fussnoten", []).extend(fns)
                continue
            if not desc["text"] and pics.get("bilder"):  # extra view, not a new mark
                target = out["merkmale"][-1] if out["merkmale"] else None
                if target is None:
                    out["uebersicht"] += pics["bilder"]
                else:
                    target["bilder"] += pics["bilder"]
                    if fns:
                        target.setdefault("fussnoten", []).extend(fns)
                continue
            paras = [p for p in desc["text"].split("\n\n") if p]
            beschr = paras[0] if paras else ""
            ort, _, merkmal = beschr.partition(": ")
            rec = {
                "nr": len(out["merkmale"]) + 1,
                "seite": side,
                "beschreibung": beschr,
                "position_traeger": ort if merkmal else None,
                "merkmal": merkmal if merkmal else None,
                "kommentar": "\n\n".join(paras[1:]) or None,
                "bildtext": pics["text"] if pics["text"] not in ("", "–") else None,
                "bilder": pics.get("bilder", []) + desc.get("bilder", []),
            }
            if fns:
                rec["fussnoten"] = fns
            out["merkmale"].append(rec)
        return out

    def extract(self) -> dict:
        m = re.match(r"Datenblatt_Provenienzforschung_(.+)_(\d{6,8})$", self.path.stem)
        self.inv = m.group(1) if m else self.path.stem
        self.img_dir = self.img_dir / self.inv
        self.img_dir.mkdir(parents=True, exist_ok=True)
        rec = {"datei": self.path.name, "seiten": self.pdf.page_count, **self.kopf()}
        rec["objekt"] = self.objekt()
        rec["provenienz"] = self.provenienz()
        rec["recherche"] = self.recherche()
        rec["anfragen"] = self.table("ANFRAGEN", ["Datum", "Institution/Archiv", "Betreff", "Antwort"],
                                     ["datum", "institution", "betreff", "antwort"])
        rec["archiv_intern"] = self.table("ARCHIV INTERN", ["Datum", "Institution/Archiv", "Bestand"],
                                          ["datum", "institution", "bestand"])
        rec["archive_extern"] = self.table("ARCHIVE EXTERN", ["Datum", "Institution/Archiv", "Bestand"],
                                           ["datum", "institution", "bestand"])
        rec["kontextinformationen"] = self.table("KONTEXTINFORMATIONEN", ["Thema", "Information"],
                                                 ["thema", "information"])
        rec["provenienzmerkmale"] = self.merkmale()
        rec["fussnoten"] = [{"nr": n, "text": t} for n, t in sorted(self.footnotes.items())]
        rec["bilder_anzahl"] = len(self.saved)
        return rec


# ---------------------------------------------------------------- output

def fn_str(r):
    return " | ".join(f"{f['nr']}: {f['text']}" for f in r.get("fussnoten", []))


def img_str(r):
    return " ; ".join(b["datei"] for b in r.get("bilder", []))


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        return
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in r.items()})


SHEET_ORDER = ["objekte", "provenienz", "merkmale", "literatur", "datenbanken", "anfragen",
               "archiv_intern", "archive_extern", "kontextinformationen", "fussnoten", "bilder"]
EXCEL_MAX_CELL = 32767


def write_xlsx(path: Path, tabs: dict[str, list[dict]]):
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    for name in SHEET_ORDER:
        rows = tabs.get(name) or []
        ws = wb.create_sheet(name)
        cols = list(dict.fromkeys(k for r in rows for k in r))
        ws.append(cols)
        for r in rows:
            vals = []
            for k in cols:
                v = r.get(k)
                if isinstance(v, (list, dict)):
                    v = json.dumps(v, ensure_ascii=False)
                if isinstance(v, str):
                    v = ILLEGAL_CHARACTERS_RE.sub("", v)[:EXCEL_MAX_CELL]
                vals.append(v)
            ws.append(vals)
        for c in ws[1]:
            c.font = Font(bold=True)
        ws.freeze_panes = "B2"
        if rows:
            ws.auto_filter.ref = ws.dimensions
        for i, k in enumerate(cols, 1):
            longest = max((len(str(r.get(k) or "")) for r in rows[:500]), default=0)
            ws.column_dimensions[get_column_letter(i)].width = max(10, min(60, max(len(k), longest) + 2))
            if longest > 60:
                for (cell,) in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
        # image paths are relative to the workbook's folder, so they open from Excel
        for i, k in enumerate(cols, 1):
            if k in ("datei",) and name == "bilder":
                for (cell,) in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                    if cell.value:
                        cell.hyperlink = cell.value
                        cell.style = "Hyperlink"
    wb.save(path)


def main(src: Path, dst: Path):
    (dst / "json").mkdir(parents=True, exist_ok=True)
    (dst / "csv").mkdir(parents=True, exist_ok=True)
    img_root = dst / "images"
    img_root.mkdir(exist_ok=True)
    docs, all_images = [], []
    tabs: dict[str, list[dict]] = {k: [] for k in (
        "objekte", "provenienz", "literatur", "datenbanken", "anfragen", "archiv_intern",
        "archive_extern", "kontextinformationen", "merkmale", "fussnoten", "bilder")}
    for pdf in ([src] if src.is_file() else sorted(src.glob("*.pdf"))):
        d = Doc(pdf, img_root)
        rec = d.extract()
        docs.append(rec)
        all_images += d.images
        (dst / "json" / f"{d.inv}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        inv = d.inv
        o = rec["objekt"]
        tabs["objekte"].append({
            "inventarnummer": inv, "datei": rec["datei"], "seiten": rec["seiten"],
            "bearbeitet_von": rec.get("bearbeitet_von"), "stand": rec.get("stand"), "stand_iso": rec.get("stand_iso"),
            **{k: o.get(k) for k in ("kuenstler", "titel", "datierung", "technik", "bezeichnung", "eigentuemer", "status")},
            "masse": " | ".join(o.get("masse", [])), "objektfoto": img_str(o),
            "inventarnummer_im_dokument": o.get("inventarnummer"),
            "provenienz_eintraege": len(rec["provenienz"]), "merkmale": len(rec["provenienzmerkmale"]["merkmale"]),
            "bilder": rec["bilder_anzahl"], "fussnoten_objekt": fn_str(o),
        })
        for i, r in enumerate(rec["provenienz"], 1):
            tabs["provenienz"].append({"inventarnummer": inv, "nr": i, "zeitraum": r["zeitraum"], "besitzer": r["besitzer"],
                                       "fussnoten": fn_str(r)})
        for r in rec["recherche"]["literatur"]:
            tabs["literatur"].append({"inventarnummer": inv, "kategorie": r["kategorie"], "text": r["text"],
                                      "bilder": img_str(r), "fussnoten": fn_str(r)})
        for r in rec["recherche"]["datenbanken"]:
            for s in r["suchen"] or [{"text": ""}]:
                tabs["datenbanken"].append({"inventarnummer": inv, "datenbank": r["datenbank"], **{
                    k: s.get(k) for k in ("treffer", "ergebnisse", "datum", "text")}, "fussnoten": fn_str(r)})
        for key in ("anfragen", "archiv_intern", "archive_extern", "kontextinformationen"):
            for r in rec[key]:
                tabs[key].append({"inventarnummer": inv, **{k: v for k, v in r.items() if k not in ("bilder", "fussnoten")},
                                  "bilder": img_str(r), "fussnoten": fn_str(r)})
        for r in rec["provenienzmerkmale"]["merkmale"]:
            tabs["merkmale"].append({"inventarnummer": inv, **{k: v for k, v in r.items() if k not in ("bilder", "fussnoten")},
                                     "bilder": img_str(r), "fussnoten": fn_str(r)})
        for f in rec["fussnoten"]:
            tabs["fussnoten"].append({"inventarnummer": inv, **f})
        print(f"{inv:>10}  pages={rec['seiten']:>2} prov={len(rec['provenienz']):>2} "
              f"marks={len(rec['provenienzmerkmale']['merkmale']):>2} imgs={rec['bilder_anzahl']:>3} fn={len(rec['fussnoten']):>2}")
    tabs["bilder"] = [{**{k: v for k, v in b.items() if k != "bbox"}, "bbox": json.dumps(b["bbox"])} for b in all_images]
    (dst / "datenblaetter.json").write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, rows in tabs.items():
        write_csv(dst / "csv" / f"{name}.csv", rows)
    write_xlsx(dst / "datenblaetter.xlsx", tabs)
    print(f"{len(docs)} documents, {len(all_images)} image placements")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
