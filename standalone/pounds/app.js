/* app.js — the pound wallet's page.
 *
 * The wiring only. fx.js looks the rates up, gbp.js does the cross rate and
 * CIBC's markup, and this file turns that into a page: it loads the rate
 * history, keeps conversions in localStorage, enforces the limit, and draws.
 *
 * Its own storage key. The euro wallet and this one are different ledgers
 * about different money, and a standalone file opened from disk shares an
 * origin with anything else opened from disk — so 'wallet.pounds.v1' rather
 * than reusing 'wallet.static.v1' and silently merging the two.
 *
 * The limit is enforced here rather than warned about. gbp.fits is the rule;
 * this file is what refuses the record, because a budget that lets you type
 * past it is a label, not a budget.
 */

'use strict';

var KEY = 'wallet.pounds.v1';
var RECENT = 40;

/* Four fifths of the limit. Far enough in to be worth saying, not so close
 * that saying it is useless. */
var NEAR = 0.8;

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
   * Greenwich in the evening — and a conversion filed on the wrong day uses
   * the wrong rate, which is the one thing this page exists to get right. */
  var now = new Date();
  return now.getFullYear() + '-' +
    String(now.getMonth() + 1).padStart(2, '0') + '-' +
    String(now.getDate()).padStart(2, '0');
}

function pounds(minor) { return FX.format(minor, 'GBP'); }
function dollars(minor) { return FX.format(minor, 'CAD'); }

/* -------------------------------------------------------------- storage */

function load() {
  try {
    var raw = window.localStorage.getItem(KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (bad) {
    return [];
  }
}

function save() {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(state.rows));
  } catch (bad) {
    say('This could not be saved — the browser refused storage. It is on ' +
        'the page but will not survive a reload.', 'bad');
  }
}

/* ----------------------------------------------------------- conversion */

/* One row's conversion, or the reason there isn't one. Cached per draw
 * rather than recomputed per cell: three cells and the total all want it. */
function converted(row) {
  if (!state.rates) { return { error: 'no rates' }; }
  try {
    var got = GBP.convertOn(row.cadMinor, row.date);
    return {
      gbp: got.poundsMinor,
      fee: got.feeMinor,
      rateDate: got.rateDate,
      lag: got.lagDays,
      cross: got.cross
    };
  } catch (bad) {
    return { error: bad.message };
  }
}

/* What the limit is measured against: the pounds actually received, which
 * is the figure after the markup rather than before it. A row that cannot
 * be converted counts as nothing and says so in its own line. */
function spent() {
  return state.rows.reduce(function (total, row) {
    var c = converted(row);
    return total + (c.error ? 0 : c.gbp);
  }, 0);
}

function spentCad() {
  return state.rows.reduce(function (total, row) {
    return total + row.cadMinor;
  }, 0);
}

/* ------------------------------------------------------------- drawing */

function draw() {
  var used = spent();
  var left = GBP.remaining(used);

  el('limitLabel').textContent =
    'Canadian dollars into pounds at the CIBC rate, against a ' +
    pounds(GBP.LIMIT_MINOR) + ' limit';

  el('spentGbp').textContent = pounds(used);
  el('leftGbp').textContent = pounds(left);
  el('spentCad').textContent = dollars(spentCad());

  drawLimit(used, left);
  drawRows();
  drawCategories();
  drawBreakdown();
  drawStorage();

  el('rateNote').textContent = state.rates
    ? (state.rates.days + ' business days of ECB rates, ' +
       FX.oldestDate() + ' to ' + FX.newestDate() +
       ' — both euro legs, crossed')
    : 'No rates loaded.';
}

function drawLimit(used, left) {
  var limit = GBP.LIMIT_MINOR;
  var share = limit ? used / limit : 0;
  var over = left < 0;
  var near = !over && share >= NEAR;

  el('limitFill').style.width = Math.min(100, Math.max(0, share * 100)) + '%';
  el('limitBar').className = 'limitBar' + (over ? ' over' : near ? ' near' : '');

  var state_ = el('limitState');
  state_.className = 'state' + (over ? ' over' : near ? ' near' : '');
  state_.textContent = over
    ? pounds(-left) + ' over the limit'
    : near ? pounds(left) + ' left — getting close'
           : pounds(left) + ' left';

  el('limitFigures').textContent =
    pounds(used) + ' of ' + pounds(limit) +
    ' (' + (Math.round(share * 1000) / 10).toFixed(1) + '%)';
}

function drawRows() {
  var rows = state.rows.slice().sort(function (a, b) {
    return a.date === b.date ? (b.added || 0) - (a.added || 0)
                             : (a.date < b.date ? 1 : -1);
  });
  var shown = rows.slice(0, RECENT);

  el('recentNote').textContent = rows.length
    ? (rows.length > RECENT ? 'newest ' + RECENT + ' of ' + rows.length
                            : rows.length + ' recorded')
    : '';

  if (!shown.length) {
    el('rows').innerHTML =
      '<tr><td colspan="7" class="empty">Nothing converted yet.</td></tr>';
    return;
  }

  el('rows').innerHTML = shown.map(function (r) {
    var c = converted(r);
    var figures = c.error
      ? '<td class="num" colspan="2"><span class="warn">' + esc(c.error) +
        '</span></td>'
      : '<td class="num">' + esc(pounds(c.gbp)) + '</td>' +
        '<td class="num">' + esc(pounds(c.fee)) + '</td>';

    var rateCell = c.error
      ? ''
      : esc(c.rateDate) + (c.lag ? ' <span class="lag">+' + c.lag + 'd</span>'
                                 : '');

    return '<tr>' +
      '<td class="mono">' + esc(r.date) + '</td>' +
      '<td>' + esc(r.description) +
        (r.category ? ' <span class="tag">' + esc(r.category) + '</span>' : '') +
        '</td>' +
      '<td class="num">' + esc(dollars(r.cadMinor)) + '</td>' +
      figures +
      '<td class="num mono">' + rateCell + '</td>' +
      '<td class="num"><button type="button" class="iconButton" data-remove="' +
        esc(r.id) + '" title="Remove">&times;</button></td>' +
      '</tr>';
  }).join('');
}

/* The category datalist. No bar chart here: the euro wallet has one because
 * a month of groceries is a shape worth seeing, and this page is a single
 * budget being drawn down, which the bar above already shows. */
function drawCategories() {
  var names = {};
  state.rows.forEach(function (r) {
    if (r.category) { names[r.category] = true; }
  });
  el('categories').innerHTML = Object.keys(names).sort().map(function (name) {
    return '<option value="' + esc(name) + '"></option>';
  }).join('');
}

function drawStorage() {
  el('storageNote').textContent =
    'Conversions are kept in this browser only, in localStorage under "' +
    KEY + '". Not synced, not backed up, and not sent anywhere — this page ' +
    'has no server to send them to.';

  el('limits').innerHTML = LIMITS.map(function (line) {
    return '<li>' + esc(line) + '</li>';
  }).join('');
}

var LIMITS = [
  'The rates are the European Central Bank’s daily reference rates, ' +
    'published on business days. They are not the rate any one bank gives ' +
    'you on any one transaction.',
  'There is no published CAD–GBP rate here: it is crossed through the ' +
    'euro from two rates that are published, both from the same day.',
  '2.5% is CIBC’s stated foreign conversion markup. A particular ' +
    'transaction can differ, and a cash withdrawal has its own fees on top.',
  'The limit is a number you set by using this page, not anything your bank ' +
    'knows about. It will not stop a card.'
];

/* ----------------------------------------------------------- the preview */

function preview() {
  var box = el('preview');
  var cents = FX.parseMoney(el('amount').value);
  var on = el('date').value;

  if (!cents || !on || !state.rates) { box.textContent = ''; return; }

  try {
    var got = GBP.convertOn(Math.abs(cents), on);
    var left = GBP.remaining(spent());
    var fitsIt = GBP.fits(spent(), got.poundsMinor);
    box.textContent = dollars(Math.abs(cents)) + ' → ' +
      pounds(got.poundsMinor) +
      (got.lagDays ? ' at the ' + got.rateDate + ' rate' : '') +
      (fitsIt ? ' — ' + pounds(left - got.poundsMinor) + ' would be left'
              : ' — over the limit by ' +
                pounds(got.poundsMinor - left));
  } catch (bad) {
    box.textContent = bad.message;
  }
}

/* --------------------------------------------------------- the breakdown */

function drawBreakdown() {
  var list = el('breakdown');
  var note = el('estNote');
  var cents = FX.parseMoney(el('estAmount').value);
  var on = el('estDate').value || (state.rates ? FX.newestDate() : today());
  var feeBp = Math.round((parseFloat(
    String(el('estFee').value).replace(',', '.')) || 0) * 100);

  if (!state.rates) { list.innerHTML = ''; note.textContent = 'No rates.'; return; }
  if (!cents) { list.innerHTML = ''; note.textContent = 'Type an amount.'; return; }
  if (feeBp < 0) { list.innerHTML = ''; note.textContent = 'A markup cannot be negative.'; return; }

  try {
    var got = GBP.convertOn(Math.abs(cents), on, feeBp);
    list.innerHTML =
      row('At the ECB cross rate', pounds(got.referenceMinor), '') +
      row('CIBC’s markup (' + (feeBp / 100).toFixed(2) + '%)',
          '−' + pounds(got.feeMinor), 'cost') +
      row('You end up with', pounds(got.poundsMinor), 'lead') +
      row('CAD per GBP', got.cross, '') +
      row('All-in, pounds per dollar', got.effectiveRate, '');

    note.textContent =
      'Crossed from the ' + got.rateDate + ' rates' +
      (got.lagDays ? ' — the last business day on or before ' + got.on : '') +
      '. The markup is charged on the Canadian side, so it is divided out ' +
      'rather than taken off the pounds.';
  } catch (bad) {
    list.innerHTML = '';
    note.textContent = bad.message;
  }
}

function row(label, value, kind) {
  return '<dt' + (kind === 'lead' ? ' class="lead"' : '') + '>' +
    esc(label) + '</dt>' +
    '<dd class="' + (kind || '') + '">' + esc(value) + '</dd>';
}

/* ------------------------------------------------------------ recording */

function record() {
  var cents = FX.parseMoney(el('amount').value);
  var on = el('date').value;
  var what = el('description').value.trim();

  if (!cents) { say('That amount could not be read.', 'bad'); return; }
  if (!on) { say('Pick a date.', 'bad'); return; }
  if (!what) { say('Say what it was.', 'bad'); return; }

  var got;
  try {
    got = GBP.convertOn(Math.abs(cents), on);
  } catch (bad) {
    say(bad.message, 'bad');
    return;
  }

  /* The limit, enforced. gbp.fits is the rule; refusing is this file's job.
   * The message says what would be needed rather than only that it failed,
   * because "no" without a number is not something you can act on. */
  var used = spent();
  if (!GBP.fits(used, got.poundsMinor)) {
    say('That would take you to ' + pounds(used + got.poundsMinor) +
        ', past the ' + pounds(GBP.LIMIT_MINOR) + ' limit. ' +
        pounds(GBP.remaining(used)) + ' is left — about ' +
        dollars(mostYouCanStillConvert(used, on)) + ' at this rate.', 'bad');
    return;
  }

  state.rows.push({
    id: String(Date.now()) + Math.random().toString(36).slice(2, 7),
    added: Date.now(),
    date: on,
    description: what,
    category: el('category').value.trim(),
    cadMinor: Math.abs(cents)
  });
  save();

  el('amount').value = '';
  el('description').value = '';
  el('preview').textContent = '';
  draw();
  say('Recorded ' + dollars(Math.abs(cents)) + ' as ' +
      pounds(got.poundsMinor) + '.', 'good');
}

/* The largest CAD amount still inside the limit, for the refusal message.
 * GBP.fromPounds rather than scaling the last answer: it is the same exact
 * ratio inverted, so the figure offered here is one that would actually be
 * accepted rather than one a penny over. */
function mostYouCanStillConvert(used, on) {
  var left = GBP.remaining(used);
  if (left <= 0) { return 0; }
  var pair = GBP.legs(on);
  return Math.max(0, GBP.fromPounds(left, pair.cad, pair.gbp));
}

/* ---------------------------------------------------------------- export */

var COLUMNS = ['Date', 'Description', 'Category', 'CAD', 'GBP', 'Markup',
               'Rate from'];

/* A record of what was converted, not an import format. The euro wallet's
 * CSV round-trips back into its own importer; this one carries the pound
 * figures beside the dollars, which is what makes it worth keeping, and
 * they are derived rather than authoritative. */
function csv() {
  var lines = [COLUMNS.join(',')];
  state.rows.slice().sort(function (a, b) {
    return a.date < b.date ? -1 : a.date > b.date ? 1 : 0;
  }).forEach(function (r) {
    var c = converted(r);
    lines.push([
      r.date,
      field(r.description),
      field(r.category || ''),
      FX.plain(r.cadMinor),
      c.error ? '' : FX.plain(c.gbp),
      c.error ? '' : FX.plain(c.fee),
      c.error ? '' : c.rateDate
    ].join(','));
  });
  return lines.join('\n') + '\n';
}

/* A field that might contain a comma or a quote. Unquoted otherwise, so the
 * common case stays readable in a text editor. */
function field(text) {
  var s = String(text == null ? '' : text);
  return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

function download() {
  if (!state.rows.length) { say('There is nothing to export.', ''); return; }
  var blob = new Blob([csv()], { type: 'text/csv;charset=utf-8' });
  var url = URL.createObjectURL(blob);
  var link = document.createElement('a');
  link.href = url;
  link.download = 'pounds-' + today() + '.csv';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
}

/* ------------------------------------------------------------------ boot */

function started(text) {
  try {
    state.rates = FX.loadRates(text);
  } catch (bad) {
    say('The rate file could not be read: ' + bad.message, 'bad');
  }
  if (state.rates && FX.columns().indexOf('GBP') < 0) {
    state.rates = null;
    say('That rate file has no GBP column, so nothing here can be ' +
        'converted. This page needs the date,CAD,GBP file beside it.', 'bad');
  }
  if (!el('estDate').value && state.rates) {
    el('estDate').value = FX.newestDate();
  }
  draw();
}

function boot() {
  state.rows = load();
  el('date').value = today();

  /* Two deliveries, one page. The standalone build inlines the history as
   * RATES_CSV because a file:// origin refuses fetch outright; the served
   * page fetches the file beside it. */
  if (typeof RATES_CSV === 'string') { started(RATES_CSV); return; }

  window.fetch('fx_rates.csv').then(function (r) {
    if (!r.ok) { throw new Error('HTTP ' + r.status); }
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

['estAmount', 'estDate', 'estFee'].forEach(function (id) {
  el(id).addEventListener('input', drawBreakdown);
  el(id).addEventListener('change', drawBreakdown);
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

el('clear').addEventListener('click', function () {
  if (!state.rows.length) { say('There is nothing stored.', ''); return; }
  if (!window.confirm('Delete all ' + state.rows.length +
                      ' conversion(s) from this browser? This cannot be undone.')) {
    return;
  }
  state.rows = [];
  save();
  draw();
  say('Deleted.', 'warn');
});

boot();
