// Paths are relative to this page, which is served from the repository root (see README).
const SCHEMA_URL = '../merkmal-schema.md';
const DATA_SOURCES = [
  { url: '../parsed/llm/merkmale.json', label: 'Regeln + LLM (parsed/llm)' },
  { url: '../parsed/merkmale.json', label: 'nur Regeln (parsed)' },
];
const EXTRACTED = '../extracted/';          // Foto and Datenblatt JSON paths are relative to this
const SHEETS = '../Provenienz_Datenblatt/';
const PHOTO_COL = 'Foto';
const INV_COL = 'Inventarnummer';
const DATE_COL = 'Datum (ISO 8601-2:2019)';
const ORIGIN_COL = 'Wertherkunft';
const SKKG_SEARCH = 'https://digital.skkg.ch/de/search?q=';

// Columns taken from the record's kontext/offen/trace rather than datensatz.
const DERIVED = {
  Seite: 'Vorder- oder Rückseite laut Datenblatt',
  Kommentar: 'Kommentar zum Merkmal im Datenblatt',
  'LLM-Auswahlgrund': 'warum die Regeln das Merkmal an das LLM geben',
  [ORIGIN_COL]: 'Methoden in der Trace: regel · llm · llm (Foto) · verweis · normalisierung',
};

// Searchable, but too free-form or unique per row to be useful as facets.
const NON_FACET = new Set([
  'Merkmal-ID', 'Quellzeile', INV_COL, 'Originalbeschreibung', 'Bemerkungen', 'Kommentar',
  PHOTO_COL, 'Datum (wie gelesen)', DATE_COL, 'LLM-Auswahlgrund',
]);
const LONG_TEXT = new Set(['Originalbeschreibung', 'Bemerkungen', 'Kommentar']);
const MAX_FACET_VALUES = 250;
const FACET_PREVIEW = 8;
const MULTI_SEP = /\s*;\s*/;
const SOURCE_LABEL = { beschreibung: 'Beschreibung', kommentar: 'Kommentar', provenienzkette: 'Provenienzkette', foto: 'Foto' };

const collator = new Intl.Collator('de-CH', { numeric: true, sensitivity: 'base' });
const $ = (sel) => document.querySelector(sel);

const state = {
  schema: [],        // [{name, values}]
  columns: [],       // display order: schema columns, then derived and extra columns
  filled: [],        // columns with at least one value
  missing: [],
  rows: [],
  facetCols: [],
  query: '',
  field: '',
  selected: new Map(),   // facet column -> Set of values
  expanded: new Set(),   // facet columns showing all values
  yearFrom: null,
  yearTo: null,
  sortCol: 'Merkmal-ID',
  sortDir: 1,
  results: [],
  current: -1,
};

// ---------- parsing ----------

const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// First table under "## Columns"; a "see below" cell takes the first line of its "### <name>" section.
function parseSchema(md) {
  const rest = md.split(/^## Columns.*$/m)[1];
  if (!rest) throw new Error('Spaltentabelle in merkmal-schema.md nicht gefunden');
  const clean = (s) => s.replace(/[`*]/g, '').trim();
  const lines = rest.split('\n');
  const start = lines.findIndex((l) => l.startsWith('|'));
  const table = [];
  for (let i = start; i >= 0 && i < lines.length && lines[i].startsWith('|'); i++) table.push(lines[i]);
  const below = (name) => clean(rest.match(new RegExp(`^### ${escRe(name)}\\s*\\n+([^\\n]+)`, 'm'))?.[1] ?? '');
  return table.slice(2)
    .map((l) => l.split('|').slice(1, -1).map(clean))
    .map(([name, values = '']) => ({ name, values: /^see below$/i.test(values) ? below(name) : values }));
}

const methodLabel = (t) => (t.methode === 'llm' && t.quelle === 'foto' ? 'llm (Foto)' : t.methode);

function toRow(rec, i) {
  const row = { _i: i, _rec: rec };
  for (const [k, v] of Object.entries(rec.datensatz)) row[k] = v == null ? '' : String(v).trim();
  row.Seite = rec.kontext?.seite ?? '';
  row.Kommentar = rec.kontext?.kommentar ?? '';
  row['LLM-Auswahlgrund'] = (rec.offen?.llm_auswahlgrund ?? []).join('; ');
  row[ORIGIN_COL] = [...new Set(rec.trace.map(methodLabel))].sort().join('; ');
  return row;
}

// ---------- text helpers ----------

const fold = (s) => s.toLowerCase().normalize('NFD').replace(/\p{M}/gu, '');

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function parseQuery(q) {
  const terms = [];
  for (const m of q.matchAll(/["«“]([^"»”]+)["»”]|(\S+)/g)) {
    const t = fold((m[1] ?? m[2]).trim());
    if (t) terms.push(t);
  }
  return terms;
}

// Highlight folded matches while keeping original characters (accents, case).
function highlight(text, terms) {
  if (!text || !terms.length) return esc(text);
  let folded = '';
  const map = [];
  for (let i = 0; i < text.length; i++) {
    for (const ch of fold(text[i])) { folded += ch; map.push(i); }
  }
  const ranges = [];
  for (const t of terms) {
    for (let p = folded.indexOf(t); p !== -1; p = folded.indexOf(t, p + 1)) {
      ranges.push([map[p], map[p + t.length - 1] + 1]);
    }
  }
  if (!ranges.length) return esc(text);
  ranges.sort((a, b) => a[0] - b[0]);
  let out = '', pos = 0;
  for (const [s, e] of ranges) {
    if (e <= pos) continue;
    const start = Math.max(s, pos);
    out += esc(text.slice(pos, start)) + '<mark>' + esc(text.slice(start, e)) + '</mark>';
    pos = e;
  }
  return out + esc(text.slice(pos));
}

const valuesOf = (row, col) => (row[col] ? row[col].split(MULTI_SEP).filter(Boolean) : []);

// Year bounds of an ISO 8601-2 value: X digits widen the range, «..» opens it.
function yearsOf(row) {
  const v = row[DATE_COL] || '';
  const ys = v.match(/(?<![\dX])\d[\dX]{3}(?![\dX])/g);
  if (!ys) return null;
  const lo = Math.min(...ys.map((y) => Number(y.replace(/X/g, '0'))));
  const hi = Math.max(...ys.map((y) => Number(y.replace(/X/g, '9'))));
  return [v.startsWith('../') ? -Infinity : lo, v.endsWith('/..') ? Infinity : hi];
}

const photoUrls = (row) => valuesOf(row, PHOTO_COL).map((p) => new URL(p, new URL(EXTRACTED, location.href)).href);
const colInfo = (name) => state.schema.find((c) => c.name === name) ?? (DERIVED[name] && { name, values: DERIVED[name] });
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString('de-CH', { dateStyle: 'medium', timeStyle: 'short' }) : '');

// ---------- loading ----------

async function fetchText(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.text();
}

function renderRun(lauf) {
  if (!lauf) return '';
  const parts = [`Regeln ${fmtDate(lauf.lauf)}`];
  for (const s of lauf.schichten ?? []) {
    if (s.methode === 'llm' && s.lauf) parts.push(`LLM ${s.modell ?? ''}${s.fotos ? ' mit Fotos' : ''}, ${s.merkmale ?? '?'} Merkmale, ${fmtDate(s.lauf)}`);
  }
  return parts.map(esc).join(' · ');
}

async function load() {
  const params = new URLSearchParams(location.search);
  const source = DATA_SOURCES.find((s) => s.url === params.get('data')) ?? DATA_SOURCES[0];
  $('#source').innerHTML = DATA_SOURCES.map((s) =>
    `<option value="${esc(s.url)}"${s === source ? ' selected' : ''}>${esc(s.label)}</option>`).join('');

  const [schemaMd, dataText] = await Promise.all([fetchText(SCHEMA_URL), fetchText(source.url)]);
  state.schema = parseSchema(schemaMd);
  const data = JSON.parse(dataText);
  $('#run').innerHTML = renderRun(data.lauf);
  state.rows = data.merkmale.map(toRow);

  const header = [...new Set(state.rows.flatMap((r) => Object.keys(r).filter((k) => !k.startsWith('_'))))];
  const known = state.schema.map((c) => c.name);
  state.columns = [...known.filter((c) => header.includes(c)), ...header.filter((h) => !known.includes(h))];
  state.missing = known.filter((c) => !header.includes(c));
  state.filled = state.columns.filter((c) => state.rows.some((r) => r[c]));

  for (const row of state.rows) {
    row._fold = Object.fromEntries(state.filled.map((c) => [c, fold(row[c])]));
    row._all = Object.values(row._fold).join('\n');
  }

  state.facetCols = state.filled.filter((c) => {
    if (NON_FACET.has(c)) return false;
    const distinct = new Set(state.rows.flatMap((r) => valuesOf(r, c)));
    return distinct.size > 1 && distinct.size <= MAX_FACET_VALUES;
  });
  if (!state.filled.includes(state.sortCol)) state.sortCol = state.filled[0];

  $('#field').innerHTML = '<option value="">allen Feldern</option>' +
    state.filled.filter((c) => c !== PHOTO_COL)
      .map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
  $('#sort').innerHTML = state.filled.filter((c) => !LONG_TEXT.has(c) && c !== PHOTO_COL)
    .map((c) => `<option value="${esc(c)}"${c === state.sortCol ? ' selected' : ''}>${esc(c)}</option>`).join('');
  $('#year-filter').hidden = !state.rows.some(yearsOf);

  buildFacets();
  update();
}

// ---------- filtering ----------

function matchesQuery(row, terms) {
  if (!terms.length) return true;
  const hay = state.field ? (row._fold[state.field] ?? '') : row._all;
  return terms.every((t) => hay.includes(t));
}

function matchesYear(row) {
  if (state.yearFrom == null && state.yearTo == null) return true;
  const ys = yearsOf(row);
  if (!ys) return false;
  return (state.yearFrom == null || ys[1] >= state.yearFrom) && (state.yearTo == null || ys[0] <= state.yearTo);
}

function matches(row, terms, exceptCol) {
  if (!matchesQuery(row, terms) || !matchesYear(row)) return false;
  for (const [col, sel] of state.selected) {
    if (col === exceptCol || !sel.size) continue;
    if (!valuesOf(row, col).some((v) => sel.has(v))) return false;
  }
  return true;
}

function compareRows(a, b) {
  const x = a[state.sortCol], y = b[state.sortCol];
  if (!x && !y) return a._i - b._i;
  if (!x) return 1;              // empty values last, whatever the direction
  if (!y) return -1;
  return state.sortDir * collator.compare(x, y) || a._i - b._i;
}

// ---------- facets ----------

function buildFacets() {
  const box = $('#facets');
  box.innerHTML = '<h2 class="facets-title">Facetten</h2>';
  for (const col of state.facetCols) {
    const info = colInfo(col);
    const sec = document.createElement('section');
    sec.className = 'facet';
    sec.dataset.col = col;
    sec.innerHTML = `
      <h3>${esc(col)} <span class="facet-sel"></span></h3>
      ${info?.values ? `<p class="facet-hint">${esc(info.values)}</p>` : ''}
      <input class="facet-find" type="search" placeholder="Werte filtern …" aria-label="${esc(col)}: Werte filtern" hidden>
      <ul class="facet-values"></ul>
      <button type="button" class="facet-more" hidden></button>`;
    box.append(sec);
  }
  if (!state.facetCols.length) box.innerHTML += '<p class="muted">Keine facettierbaren Spalten.</p>';
}

function renderFacets(terms) {
  for (const sec of document.querySelectorAll('.facet')) {
    const col = sec.dataset.col;
    const sel = state.selected.get(col) ?? new Set();
    const counts = new Map();
    for (const row of state.rows) {
      if (!matches(row, terms, col)) continue;
      for (const v of new Set(valuesOf(row, col))) counts.set(v, (counts.get(v) ?? 0) + 1);
    }
    for (const v of sel) if (!counts.has(v)) counts.set(v, 0);

    const find = sec.querySelector('.facet-find');
    find.hidden = counts.size <= FACET_PREVIEW;
    const needle = fold(find.value.trim());
    let entries = [...counts].sort((a, b) =>
      (sel.has(b[0]) - sel.has(a[0])) || (b[1] - a[1]) || collator.compare(a[0], b[0]));
    if (needle) entries = entries.filter(([v]) => sel.has(v) || fold(v).includes(needle));

    const expanded = state.expanded.has(col) || needle;
    const shown = expanded ? entries : entries.slice(0, FACET_PREVIEW);
    sec.querySelector('.facet-values').innerHTML = shown.map(([v, n]) => `
      <li><label class="${n === 0 ? 'zero' : ''}">
        <input type="checkbox" value="${esc(v)}"${sel.has(v) ? ' checked' : ''}>
        <span class="facet-label">${esc(v)}</span><span class="count">${n}</span>
      </label></li>`).join('') || '<li class="muted">Keine Werte</li>';

    const more = sec.querySelector('.facet-more');
    more.hidden = needle || entries.length <= FACET_PREVIEW;
    more.textContent = state.expanded.has(col) ? 'Weniger anzeigen' : `Alle ${entries.length} anzeigen`;
    sec.querySelector('.facet-sel').textContent = sel.size ? `(${sel.size})` : '';
    sec.classList.toggle('active', sel.size > 0);
  }
}

// ---------- results ----------

function renderChips() {
  const chips = [];
  if (state.query) chips.push({ label: `Suche: «${state.query}»${state.field ? ` in ${state.field}` : ''}`, clear: () => { state.query = ''; $('#q').value = ''; } });
  for (const [col, sel] of state.selected) {
    for (const v of sel) chips.push({ label: `${col}: ${v}`, clear: () => sel.delete(v) });
  }
  if (state.yearFrom != null || state.yearTo != null) {
    chips.push({ label: `Jahr: ${state.yearFrom ?? '…'}–${state.yearTo ?? '…'}`, clear: () => {
      state.yearFrom = state.yearTo = null; $('#year-from').value = ''; $('#year-to').value = '';
    } });
  }
  const box = $('#chips');
  box.innerHTML = '';
  chips.forEach((c) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'chip';
    b.innerHTML = `${esc(c.label)} <span aria-hidden="true">×</span>`;
    b.setAttribute('aria-label', `Filter entfernen: ${c.label}`);
    b.onclick = () => { c.clear(); update(); };
    box.append(b);
  });
  if (chips.length > 1) {
    const all = document.createElement('button');
    all.type = 'button';
    all.className = 'chip chip-clear';
    all.textContent = 'Alle zurücksetzen';
    all.onclick = resetAll;
    box.append(all);
  }
}

function img(src, cls) {
  const alt = `Foto ${src?.split('/').pop().replace(/\.\w+$/, '') ?? ''}`;
  return src
    ? `<img class="${cls}" src="${esc(src)}" alt="${esc(alt)}" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'${cls} noimg',textContent:'Bild fehlt'}))">`
    : `<div class="${cls} noimg">Kein Foto</div>`;
}

function cardTitle(row) {
  return row['Urheber (normalisiert)'] || row['Art des Urhebers'] || row.Typ || 'Merkmal';
}

function renderGrid(terms) {
  const meta = (row) => [
    row['Merkmal-ID'], row.Typ, row.Anbringungsort,
  ].filter(Boolean).map(esc).join(' · ');
  return `<div class="grid">${state.results.map((row, k) => {
    const photos = photoUrls(row);
    return `
    <button type="button" class="card" data-k="${k}">
      ${img(photos[0], 'card-img')}${photos.length > 1 ? `<span class="card-count">+${photos.length - 1}</span>` : ''}
      <span class="card-body">
        <span class="card-title">${highlight(cardTitle(row), terms)}</span>
        <span class="card-meta">${meta(row)}</span>
        <span class="card-desc">${highlight(row.Originalbeschreibung, terms)}</span>
      </span>
    </button>`;
  }).join('')}</div>`;
}

function update() {
  const terms = parseQuery(state.query);
  state.results = state.rows.filter((r) => matches(r, terms)).sort(compareRows);
  renderFacets(terms);
  renderChips();
  $('#stats').textContent = `${state.results.length} von ${state.rows.length} Merkmalen`;
  $('#sort-dir').textContent = state.sortDir > 0 ? '↑' : '↓';
  $('#sort').value = state.sortCol;
  $('#results').innerHTML = state.results.length
    ? renderGrid(terms)
    : '<p class="empty">Keine Treffer. Suchbegriff ändern oder Filter entfernen.</p>';
}

function resetAll() {
  state.query = ''; state.field = ''; state.yearFrom = state.yearTo = null;
  state.selected.clear();
  $('#q').value = ''; $('#field').value = ''; $('#year-from').value = ''; $('#year-to').value = '';
  document.querySelectorAll('.facet-find').forEach((i) => { i.value = ''; });
  update();
}

// ---------- detail ----------

// Text a trace span points into: LLM spans and the comment-based rules name it in «quelle».
const spanSource = (t) => (t.quelle === 'kommentar' ? 'Kommentar'
  : t.methode !== 'llm' || t.quelle === 'beschreibung' ? 'Originalbeschreibung' : null);

const quote = (s) => (/[«»]/.test(s ?? '') ? esc(s) : `«${esc(s)}»`);

function renderTrace(t, k) {
  const src = t.span && spanSource(t);
  const attrs = src ? ` tabindex="0" data-t="${k}" title="Stelle in ${esc(src)} markieren"` : '';
  let body;
  if (t.methode === 'llm') {
    body = `${esc(SOURCE_LABEL[t.quelle] ?? t.quelle)}: ${quote(t.zitat)}` +
      (t.begruendung ? `<span class="trace-why">${esc(t.begruendung)}</span>` : '');
  } else if (t.methode === 'normalisierung') {
    body = `aus ${quote(t.von)} <code>${esc(t.regel)}</code>`;
  } else {
    const from = t.methode === 'verweis' || t.quelle === 'kommentar' ? `${esc(SOURCE_LABEL[t.quelle] ?? '')} ` : '';
    body = `<code>${esc(t.regel)}</code> ${from}${quote(t.zitat ?? t.quelle)}${t.von ? ` (von ${esc(t.von)})` : ''}`;
  }
  return `<li class="trace-item"${attrs}><span class="method m-${esc(methodLabel(t).replace(/\W+/g, '-'))}">${esc(methodLabel(t))}</span> ${body}</li>`;
}

const workCache = new Map();
function fetchWork(inv) {
  if (!workCache.has(inv)) {
    workCache.set(inv, fetch(new URL(`json/${encodeURIComponent(inv)}.json`, new URL(EXTRACTED, location.href)))
      .then((r) => (r.ok ? r.json() : null)).catch(() => null));
  }
  return workCache.get(inv);
}

async function fillWork(inv) {
  const doc = await fetchWork(inv);
  const box = document.querySelector(`#work[data-inv="${CSS.escape(inv)}"]`);
  if (!doc || !box) return;
  const o = doc.objekt ?? {};
  box.innerHTML = `
    <p class="work-title">${esc(o.kuenstler ?? '')}${o.titel ? `, <i>${esc(o.titel)}</i>` : ''}${o.datierung ? `, ${esc(o.datierung)}` : ''}</p>
    <p><a href="${esc(SHEETS + encodeURIComponent(doc.datei))}" target="_blank" rel="noopener">Datenblatt (PDF), Stand ${esc(doc.stand ?? '')}</a></p>`;
}

function openDetail(k) {
  state.current = k;
  const row = state.results[k];
  const terms = parseQuery(state.query);
  const photos = photoUrls(row);
  const traces = row._rec.trace;
  const byField = new Map();
  traces.forEach((t, i) => { if (!byField.has(t.feld)) byField.set(t.feld, []); byField.get(t.feld).push(i); });

  const value = (c) => (c === PHOTO_COL
    ? valuesOf(row, c).map((p, i) => `<a href="${esc(photos[i])}" target="_blank" rel="noopener">${esc(p)}</a>`).join('<br>')
    : highlight(row[c], terms));
  const field = (c) => {
    const info = colInfo(c);
    const tr = byField.get(c);
    return `<div class="field"><dt>${esc(c)}${info?.values && state.facetCols.includes(c) ? `<small>${esc(info.values)}</small>` : ''}</dt>
      <dd><div class="value" data-text="${esc(c)}">${value(c)}</div>
      ${tr ? `<ul class="trace">${tr.map((i) => renderTrace(traces[i], i)).join('')}</ul>` : ''}</dd></div>`;
  };
  const filledCols = state.columns.filter((c) => row[c]);
  const emptyCols = [...state.columns.filter((c) => !row[c]), ...state.missing];
  const inv = row[INV_COL];
  const transcriptions = row._rec.kontext?.transkriptionen ?? [];

  $('#detail-body').innerHTML = `
    <header class="detail-head">
      <h2 id="detail-title">${esc(row['Merkmal-ID'])} – ${esc(cardTitle(row))}</h2>
      <div class="detail-nav">
        <button type="button" data-nav="-1" ${k === 0 ? 'disabled' : ''} aria-label="Vorheriges Merkmal">←</button>
        <span>${k + 1} / ${state.results.length}</span>
        <button type="button" data-nav="1" ${k === state.results.length - 1 ? 'disabled' : ''} aria-label="Nächstes Merkmal">→</button>
        <button type="button" data-close aria-label="Schliessen">✕</button>
      </div>
    </header>
    <div class="detail-grid">
      <figure>
        ${photos.length
          ? photos.map((src) => `<a href="${esc(src)}" target="_blank" rel="noopener" title="Bild in voller Grösse öffnen">${img(src, 'detail-img')}</a>`).join('')
          : img(null, 'detail-img')}
        <figcaption>
          <div id="work" data-inv="${esc(inv)}"></div>
          <a href="${SKKG_SEARCH}${encodeURIComponent(inv)}" target="_blank" rel="noopener">Inv. ${esc(inv)} in SKKG Sammlung Digital ↗</a>
          ${transcriptions.length ? `<p class="muted">Transkriptionen: ${transcriptions.map(esc).join(' · ')}</p>` : ''}
        </figcaption>
      </figure>
      <div>
        <dl class="fields">${filledCols.map(field).join('')}</dl>
        ${emptyCols.length ? `<details class="empty-fields"><summary>Leere Felder (${emptyCols.length})</summary>
          <p>${emptyCols.map(esc).join(' · ')}</p></details>` : ''}
      </div>
    </div>`;
  const dlg = $('#detail');
  if (!dlg.open) dlg.showModal();
  fillWork(inv);
}

// Mark a trace span in the text it points into; null restores the search highlighting.
function showSpan(t) {
  const row = state.results[state.current];
  const terms = parseQuery(state.query);
  for (const box of document.querySelectorAll('#detail [data-text]')) {
    const col = box.dataset.text;
    if (col !== 'Originalbeschreibung' && col !== 'Kommentar') continue;
    const text = row[col];
    if (t && spanSource(t) === col) {
      const [s, e] = t.span;
      box.innerHTML = `${esc(text.slice(0, s))}<mark class="span">${esc(text.slice(s, e))}</mark>${esc(text.slice(e))}`;
    } else box.innerHTML = highlight(text, terms);
  }
}

// ---------- events ----------

let qTimer;
$('#q').addEventListener('input', (e) => {
  clearTimeout(qTimer);
  qTimer = setTimeout(() => { state.query = e.target.value.trim(); update(); }, 150);
});
$('#field').addEventListener('change', (e) => { state.field = e.target.value; update(); });
for (const id of ['#year-from', '#year-to']) {
  $(id).addEventListener('change', () => {
    const num = (v) => (/^\d{4}$/.test(v) ? Number(v) : null);
    state.yearFrom = num($('#year-from').value);
    state.yearTo = num($('#year-to').value);
    update();
  });
}
$('#sort').addEventListener('change', (e) => { state.sortCol = e.target.value; update(); });
$('#sort-dir').addEventListener('click', () => { state.sortDir *= -1; update(); });
$('#source').addEventListener('change', (e) => {
  const p = new URLSearchParams(location.search);
  p.set('data', e.target.value);
  location.search = p.toString();
});

$('#facets').addEventListener('change', (e) => {
  if (e.target.type !== 'checkbox') return;
  const col = e.target.closest('.facet').dataset.col;
  if (!state.selected.has(col)) state.selected.set(col, new Set());
  const sel = state.selected.get(col);
  e.target.checked ? sel.add(e.target.value) : sel.delete(e.target.value);
  update();
});
$('#facets').addEventListener('input', (e) => {
  if (e.target.classList.contains('facet-find')) renderFacets(parseQuery(state.query));
});
$('#facets').addEventListener('click', (e) => {
  if (!e.target.classList.contains('facet-more')) return;
  const col = e.target.closest('.facet').dataset.col;
  state.expanded.has(col) ? state.expanded.delete(col) : state.expanded.add(col);
  renderFacets(parseQuery(state.query));
});

$('#results').addEventListener('click', (e) => {
  const hit = e.target.closest('[data-k]');
  if (hit) openDetail(Number(hit.dataset.k));
});

const dlg = $('#detail');
dlg.addEventListener('click', (e) => {
  if (e.target === dlg || e.target.closest('[data-close]')) dlg.close();
  const nav = e.target.closest('[data-nav]');
  if (nav) openDetail(state.current + Number(nav.dataset.nav));
});
dlg.addEventListener('keydown', (e) => {
  if (e.target.closest('input, select')) return;
  if (e.key === 'ArrowLeft' && state.current > 0) openDetail(state.current - 1);
  if (e.key === 'ArrowRight' && state.current < state.results.length - 1) openDetail(state.current + 1);
});
const traceOf = (e) => e.target.closest?.('[data-t]');
for (const [on, off] of [['mouseover', 'mouseout'], ['focusin', 'focusout']]) {
  dlg.addEventListener(on, (e) => {
    const li = traceOf(e);
    if (li) showSpan(state.results[state.current]._rec.trace[Number(li.dataset.t)]);
  });
  dlg.addEventListener(off, (e) => { if (traceOf(e)) showSpan(null); });
}

load().catch((err) => {
  const fileHint = location.protocol === 'file:'
    ? '<p>Die Seite wurde direkt als Datei geöffnet. Browser blockieren dann das Laden der Daten. Im Projektordner <code>python3 -m http.server 8000</code> starten und <a href="http://localhost:8000/ui/">http://localhost:8000/ui/</a> öffnen.</p>'
    : '';
  $('#results').innerHTML = `<div class="error"><p><strong>Daten konnten nicht geladen werden.</strong> ${esc(err.message)}</p>${fileHint}</div>`;
});
