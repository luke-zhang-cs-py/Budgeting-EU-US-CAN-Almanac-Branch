/* app.js — the static wallet's page.
 *
 * No logic of its own worth the name: every figure comes from fx.js, which is
 * the checked port of fx/fxrates.py, core/money.py and fx/fxcost.py. This file loads the
 * rate history, keeps purchases in localStorage, and draws.
 *
 * The rate file is a snapshot. It has a newest date, and the page says what
 * it is rather than letting a reader assume the figures are current — a
 * purchase after that date on a weekday is refused here exactly as the server
 * refuses it, because converting at a stale rate is a guess that would be
 * silently replaced the moment the real one published.
 */

'use strict';

var KEY = 'wallet.static.v1';
var RECENT = 40;

var state = { rates: null, rows: [] };

/* ------------------------------------------------------------- helpers */

function el(id) { return document.getElementById(id); }

function esc(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
             '"': '&quot;', "'": '&#39;' }[c];
  });
}

function say(message, kind) {
  var box = el('notice');
  box.textContent = message || '';
  box.className = 'notice' + (kind ? ' ' + kind : '');
  box.hidden = !message;
}

function today() {
  /* Local time. toISOString is UTC, which is yesterday for anyone west of
   * Greenwich in the evening — and a purchase filed on the wrong day converts
   * at the wrong rate, which is the one thing this page exists to get right. */
  var now = new Date();
  return now.getFullYear() + '-' +
    String(now.getMonth() + 1).padStart(2, '0') + '-' +
    String(now.getDate()).padStart(2, '0');
}

function monthOf(iso) { return iso.slice(0, 7); }

function prettyMonth(month) {
  var names = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
               'August', 'September', 'October', 'November', 'December'];
  var parts = month.split('-');
  return names[+parts[1] - 1] + ' ' + parts[0];
}

/* -------------------------------------------------------------- storage */

function load() {
  try {
    var raw = window.localStorage.getItem(KEY);
    state.rows = raw ? JSON.parse(raw) : [];
  } catch (bad) { state.rows = []; }
  if (!Array.isArray(state.rows)) { state.rows = []; }
}

function save() {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(state.rows));
  } catch (bad) {
    say('This browser would not save that — private mode, or storage is ' +
        'full. The row is on screen but will not survive a reload.', 'bad');
  }
}

/* ---------------------------------------------------------- conversion */

/* One row, converted. The conversion is attempted per currency so that a
 * date the rate file cannot answer for still shows its euro figure and says
 * why, rather than the row vanishing. */
function converted(row) {
  var out = { eur: row.minor, cad: null, usd: null, rateDate: null,
              lag: null, error: null };
  try {
    var cad = FX.convertOn(row.minor, row.date, 'CAD');
    out.cad = cad.minor;
    out.rateDate = cad.rateDate;
    out.lag = cad.lagDays;
  } catch (bad) { out.error = bad.message; }
  try {
    out.usd = FX.convertOn(row.minor, row.date, 'USD').minor;
  } catch (bad) { out.error = out.error || bad.message; }
  return out;
}

/* ------------------------------------------------------------- drawing */

function draw() {
  var month = state.rows.length
    ? monthOf(state.rows[state.rows.length - 1].date) : monthOf(today());
  el('monthLabel').textContent = prettyMonth(month);

  var inMonth = state.rows.filter(function (r) {
    return monthOf(r.date) === month;
  });

  var eur = 0, cad = 0, usd = 0, unconverted = 0;
  inMonth.forEach(function (r) {
    var c = converted(r);
    eur += c.eur;
    if (c.cad === null || c.usd === null) { unconverted++; return; }
    cad += c.cad;
    usd += c.usd;
  });

  el('spentEur').textContent = FX.format(eur, 'EUR');
  el('spentCad').textContent = FX.format(cad, 'CAD');
  el('spentUsd').textContent = FX.format(usd, 'USD');

  drawRows();
  drawCategories(inMonth);

  el('storageNote').textContent =
    'In this browser only — ' + state.rows.length + ' purchase(s) in ' +
    'localStorage under "' + KEY + '". Not synced, not backed up, and not ' +
    'visible to the full app: export the CSV and import it there, which is ' +
    'what the four column names are for.' +
    (unconverted ? ' ' + unconverted + ' row(s) this month have no ' +
     'conversion — their dates are outside what the shipped rate file ' +
     'covers.' : '');
}

function drawRows() {
  var recent = state.rows.slice().reverse().slice(0, RECENT);
  if (!recent.length) {
    el('rows').innerHTML =
      '<tr><td colspan="7" class="empty">Nothing recorded yet.</td></tr>';
    el('recentNote').textContent = '';
    return;
  }
  el('recentNote').textContent = 'the last ' + recent.length +
    ' of ' + state.rows.length;

  el('rows').innerHTML = recent.map(function (r) {
    var c = converted(r);
    var money = c.error
      ? '<td class="num" colspan="2"><span class="warn">' + esc(c.error) +
        '</span></td>'
      : '<td class="num">' + esc(FX.format(c.cad, 'CAD')) + '</td>' +
        '<td class="num">' + esc(FX.format(c.usd, 'USD')) + '</td>';
    /* Built and escaped here, then composed below. The name says it is a
     * fragment rather than a value, which is what keeps the escaping guard
     * in tests/test_frontend.py honest: it allows a fragment by name, so a
     * generic one like `from` would let a real value through later. */
    var rateCell = c.rateDate
      ? esc(c.rateDate) + (c.lag ? ' <span class="lag">+' + c.lag + 'd</span>'
                                 : '')
      : '—';
    /* Everything escaped, including what looks safe: a description can come
     * from an imported CSV and a category is whatever somebody typed. */
    return '<tr>' +
      '<td class="mono">' + esc(r.date) + '</td>' +
      '<td>' + esc(r.description) +
        (r.category ? ' <span class="tag">' + esc(r.category) + '</span>' : '') +
      '</td>' +
      '<td class="num">' + esc(FX.format(r.minor, 'EUR')) + '</td>' +
      money +
      '<td class="num mono">' + rateCell + '</td>' +
      '<td class="num"><button class="iconButton" data-remove="' +
        esc(r.id) + '" title="Remove">&times;</button></td>' +
      '</tr>';
  }).join('');
}

function drawCategories(inMonth) {
  var totals = {}, seen = {};
  state.rows.forEach(function (r) {
    if (r.category) { seen[r.category] = true; }
  });
  el('categories').innerHTML = Object.keys(seen).map(function (name) {
    return '<option value="' + esc(name) + '"></option>';
  }).join('');

  inMonth.forEach(function (r) {
    var name = r.category || 'Uncategorised';
    totals[name] = (totals[name] || 0) + Math.abs(r.minor);
  });

  var rows = Object.keys(totals).map(function (name) {
    return { name: name, minor: totals[name] };
  }).sort(function (a, b) { return b.minor - a.minor; });

  if (!rows.length) {
    el('categoryBars').innerHTML = '<p class="empty">Nothing this month.</p>';
    el('categoryNote').textContent = '';
    return;
  }

  var peak = rows[0].minor;
  el('categoryBars').innerHTML = rows.map(function (row) {
    var width = (row.minor / peak * 100).toFixed(1);
    return '<div class="bar"><span class="lab">' + esc(row.name) + '</span>' +
      '<span class="rail"><span class="fill" style="width:' + width +
      '%"></span></span>' +
      '<span class="val">' + esc(FX.format(row.minor, 'EUR')) + '</span></div>';
  }).join('');
  el('categoryNote').textContent =
    'One measure, so one colour and no legend — every bar is labelled ' +
    'directly. Euro figures, because that is what was spent; the conversion ' +
    'belongs to a row and its date, not to a total.';
}

/* ------------------------------------------------------------ the form */

function preview() {
  var minor = FX.parseMoney(el('amount').value);
  var date = el('date').value;
  if (!minor || !date) { el('preview').textContent = ''; return; }
  try {
    var cad = FX.convertOn(minor, date, 'CAD');
    var usd = FX.convertOn(minor, date, 'USD');
    el('preview').textContent =
      FX.format(minor, 'EUR') + ' → ' + FX.format(cad.minor, 'CAD') + ' / ' +
      FX.format(usd.minor, 'USD') + '  at ' + cad.rate + ' from ' +
      cad.rateDate + (cad.lagDays ? ' (' + cad.lagDays + ' days back)' : '');
  } catch (bad) {
    el('preview').textContent = bad.message;
  }
}

function record() {
  var minor = FX.parseMoney(el('amount').value);
  if (!minor) {
    say('That amount could not be read. Both 3.50 and 3,50 work.', 'bad');
    return;
  }
  var description = el('description').value.trim();
  if (!description) { say('It needs a description.', 'bad'); return; }
  var date = el('date').value || today();

  try {
    FX.rate(date, 'CAD');
  } catch (bad) {
    say(bad.message, 'bad');
    return;
  }

  state.rows.push({
    id: String(Date.now()) + Math.random().toString(36).slice(2, 7),
    date: date,
    description: description,
    category: el('category').value.trim(),
    minor: Math.abs(minor)
  });
  state.rows.sort(function (a, b) { return a.date < b.date ? -1 : 1; });
  save();

  say('Recorded. ' + FX.format(Math.abs(minor), 'EUR'), 'good');
  el('amount').value = '';
  el('description').value = '';
  el('preview').textContent = '';
  draw();
}

/* ------------------------------------------------------- the estimate */

function drawEstimate() {
  var minor = FX.parseMoney(el('estAmount').value);
  var percent = parseFloat(el('estFee').value);
  var currency = el('estCurrency').value;

  if (!minor || !(percent >= 0)) {
    el('estimate').textContent = 'Give an amount and a fee.';
    return;
  }

  var bp = Math.round(percent * 100);
  try {
    var newest = FX.newestDate();
    var found = FX.rate(newest, currency);
    var got = FX.estimate(minor, found.rate, bp);
    el('estimate').textContent = [
      FX.format(got.baseMinor, 'EUR') + ' at ' + found.rate + '   ' +
        FX.format(got.convertedMinor, currency),
      'card fee ' + percent.toFixed(2) + '%' +
        '   +' + FX.format(got.feeMinor, currency),
      'you would be charged   ' + FX.format(got.totalMinor, currency) +
        '   all-in rate ' + got.effectiveRate,
      '',
      'published ' + found.from + ' · a daily reference rate, not a live one'
    ].join('\n');
  } catch (bad) {
    el('estimate').textContent = bad.message;
  }
}

/* ------------------------------------------------------------ the file */

var COLUMNS = ['Date', 'Description', 'Amount', 'Currency'];

function csv() {
  var lines = [COLUMNS.join(',')];
  state.rows.forEach(function (r) {
    var what = r.description + (r.category ? ' - ' + r.category : '');
    var safe = /[",\n]/.test(what)
      ? '"' + what.replace(/"/g, '""') + '"' : what;
    /* plain, not format: a grouped "1,234.56" in a comma-separated file
     * splits into two columns at the far end. */
    lines.push([r.date, safe, '-' + FX.plain(r.minor), 'EUR'].join(','));
  });
  return lines.join('\n') + '\n';
}

function download() {
  if (!state.rows.length) { say('Nothing to export yet.', ''); return; }
  var blob = new Blob([csv()], { type: 'text/csv;charset=utf-8' });
  var url = URL.createObjectURL(blob);
  var link = document.createElement('a');
  link.href = url;
  link.download = 'wallet-' + state.rows[0].date + '-to-' +
    state.rows[state.rows.length - 1].date + '.csv';
  link.click();
  URL.revokeObjectURL(url);
  say(state.rows.length + ' purchase(s) exported. The four column names are ' +
      'the ones the full app imports without a mapping.', 'good');
}

function importCsv(text) {
  var lines = text.trim().split(/\r?\n/);
  if (!lines.length) { say('That file was empty.', 'bad'); return; }

  var header = lines[0].split(',').map(function (h) { return h.trim(); });
  var at = {};
  COLUMNS.forEach(function (name) { at[name] = header.indexOf(name); });
  if (at.Date < 0 || at.Amount < 0) {
    say('That file has no Date and Amount columns, so there is nothing to ' +
        'read. The export from this page has the right ones.', 'bad');
    return;
  }

  var added = 0, skipped = 0;
  for (var i = 1; i < lines.length; i++) {
    var cells = splitCsvLine(lines[i]);
    var minor = FX.parseMoney(cells[at.Amount] || '');
    var date = (cells[at.Date] || '').trim();
    if (!minor || !/^\d{4}-\d{2}-\d{2}$/.test(date)) { skipped++; continue; }
    state.rows.push({
      id: String(Date.now()) + i + Math.random().toString(36).slice(2, 5),
      date: date,
      description: (at.Description >= 0 ? cells[at.Description] : '') || '—',
      category: '',
      minor: Math.abs(minor)
    });
    added++;
  }
  state.rows.sort(function (a, b) { return a.date < b.date ? -1 : 1; });
  save();
  draw();
  say('Imported ' + added + ' row(s)' +
      (skipped ? ', skipped ' + skipped + ' without a readable date and ' +
       'amount' : '') + '.', added ? 'good' : 'warn');
}

/* Quoted fields with commas in them, and doubled quotes inside. The one rule
 * every CSV reader agrees on, and the same one the export writes. */
function splitCsvLine(line) {
  var out = [], field = '', quoted = false, i;
  for (i = 0; i < line.length; i++) {
    var ch = line.charAt(i);
    if (quoted) {
      if (ch === '"' && line.charAt(i + 1) === '"') { field += '"'; i++; }
      else if (ch === '"') { quoted = false; }
      else { field += ch; }
    } else if (ch === '"') { quoted = true; }
    else if (ch === ',') { out.push(field); field = ''; }
    else { field += ch; }
  }
  out.push(field);
  return out;
}

/* ------------------------------------------------------- what it cannot do */

var LIMITS = [
  'Read a bank export. OFX and QFX carry the bank&rsquo;s own rate and its ' +
    'id for each transaction, and reading them needs the full app.',
  'Watch a folder, or import anything on its own.',
  'Keep the picture of a purchase. The <a href="../capture/">capture page</a> ' +
    'reads a screenshot in the browser; this page takes the figures.',
  'Reconcile against a statement, or measure what a conversion actually cost ' +
    '— that needs what the bank billed, which only an export carries.',
  'Follow you to another browser. There is no account and nothing is synced.'
];

/* LIMITS is authored prose with intentional markup -- one of the entries
 * links to the capture page -- so it is written through rather than escaped,
 * exactly as RULES and BUGS are on the overview page. Nothing in it comes
 * from a user, and the parameter is named for that so the escaping guard's
 * allowance cannot quietly start covering a real value. */
function drawLimits() {
  el('limits').innerHTML = LIMITS.map(function (authoredHtml) {
    return '<li>' + authoredHtml + '</li>';
  }).join('');
}

/* ---------------------------------------------------------------- boot */

function started(text) {
  var info = FX.loadRates(text);
  el('rateNote').textContent =
    info.days.toLocaleString() + ' business days of ECB rates, ' +
    FX.oldestDate() + ' to ' + info.newest + '. A snapshot shipped with ' +
    'this page — a purchase after that date is refused rather than ' +
    'converted at a stale rate.';
  draw();
  drawEstimate();
}

function boot() {
  el('date').value = today();
  load();
  drawLimits();

  /* The rates arrive one of two ways, and the page has to work both.
   *
   * Built as a single file, they are already here as RATES_CSV — which is
   * not an optimisation but the only thing that works: a file opened by
   * double-clicking has a file:// origin, and fetch is refused there. As a
   * folder served over HTTP, they are fetched. One app.js either way,
   * because a second copy of this page would be a second copy of every rule
   * in it. */
  if (typeof RATES_CSV === 'string') {
    started(RATES_CSV);
    return;
  }

  fetch('fx_rates.csv').then(function (r) {
    if (!r.ok) { throw new Error('the rate file did not load (' + r.status + ')'); }
    return r.text();
  }).then(started).catch(function (bad) {
    say('No rates: ' + bad.message + '. Everything else still works, but ' +
        'nothing can be converted.', 'bad');
    draw();
  });
}

/* --------------------------------------------------------------- wiring */

el('addForm').addEventListener('submit', function (event) {
  event.preventDefault();
  record();
});
el('amount').addEventListener('input', preview);
el('date').addEventListener('change', preview);

['estAmount', 'estFee', 'estCurrency'].forEach(function (id) {
  el(id).addEventListener('input', drawEstimate);
  el(id).addEventListener('change', drawEstimate);
});

el('rows').addEventListener('click', function (event) {
  var id = event.target.dataset && event.target.dataset.remove;
  if (!id) { return; }
  state.rows = state.rows.filter(function (r) { return r.id !== id; });
  save();
  draw();
  say('Removed.', '');
});

el('export').addEventListener('click', download);
el('export2').addEventListener('click', download);

el('importBtn').addEventListener('click', function () {
  el('importFile').click();
});
el('importFile').addEventListener('change', function () {
  var file = this.files && this.files[0];
  this.value = '';
  if (!file) { return; }
  var reader = new FileReader();
  reader.onload = function () { importCsv(String(reader.result)); };
  reader.onerror = function () { say('That file could not be read.', 'bad'); };
  reader.readAsText(file);
});

el('clear').addEventListener('click', function () {
  if (!state.rows.length) { say('There is nothing stored.', ''); return; }
  if (!window.confirm('Delete all ' + state.rows.length +
                      ' purchase(s) from this browser? This cannot be undone.')) {
    return;
  }
  state.rows = [];
  save();
  draw();
  say('Deleted.', 'warn');
});

boot();
