/* fx.js — the rate lookup, the money, and the card fee, in the browser.
 *
 * A deliberate second implementation of fx/fxrates.py, core/money.py and
 * fx/fxcost.py,
 * and the second one in this project after docs/capture/rules.js. The static
 * page has no server to ask, so the arithmetic has to exist here too — and
 * two implementations of anything drift, so this one is not trusted to stay
 * in step. `cases.json` beside this file holds the shared cases, the Python
 * suite asserts fxrates/money/fxcost produce those answers, and a
 * headless-browser test asserts this file produces the same ones. If either
 * side changes a rule and not the other, a test fails rather than the two
 * quietly disagreeing about what a purchase cost.
 *
 * Three rules carry over exactly, because each one is a way of being wrong:
 *
 *   1. The rate is the one published on the day of the purchase, or the most
 *      recent business day before it. The ECB publishes on business days
 *      only — no weekends, and the longest gap in the series is five days.
 *   2. It never reaches *forward*. 5 April 2026 is one day from the 7th and
 *      three from the 2nd, but the 7th had not happened when the money was
 *      spent, and using it would make a closed month's total change every
 *      time the file was refreshed.
 *   3. A date past the newest rate is refused only on a weekday. A Sunday
 *      purchase is later than Friday's rate but its own rate is never
 *      coming, so refusing it left weekend spending with no figure at all.
 *
 * Money is integer minor units throughout, and every division rounds half
 * away from zero on integers rather than going near a float — 0.1 + 0.2 is
 * not 0.3, and a total that disagrees with its own rows is how a reader
 * stops trusting every other figure on the page.
 */

'use strict';

var FX = (function () {

  var BASE = 'EUR';
  var TARGETS = ['CAD', 'USD'];
  var MAX_LOOKBACK_DAYS = 10;   // fxrates.MAX_LOOKBACK_DAYS
  var BASIS_POINTS = 10000;     // fxcost.BASIS_POINTS
  var MINOR_UNITS = 2;

  var rates = null;             // { 'YYYY-MM-DD': { CAD: '1.6043', ... } }
  var columns = [];             // the currencies that table carries
  var newest = null;

  /* ------------------------------------------------------------- errors */

  function RateError(message) { this.name = 'RateError'; this.message = message; }
  RateError.prototype = new Error();

  /* -------------------------------------------------------------- dates */

  function utc(iso) {
    var p = iso.split('-');
    return Date.UTC(+p[0], +p[1] - 1, +p[2]);
  }
  function isoOf(ms) {
    var d = new Date(ms);
    return d.getUTCFullYear() + '-' +
      String(d.getUTCMonth() + 1).padStart(2, '0') + '-' +
      String(d.getUTCDate()).padStart(2, '0');
  }
  function minusDays(iso, n) { return isoOf(utc(iso) - n * 86400000); }
  /* Monday is 0, to match Python's date.weekday(). */
  function weekday(iso) { return (new Date(utc(iso)).getUTCDay() + 6) % 7; }

  /* -------------------------------------------------------------- money */

  /* n / d, rounded half away from zero, on integers.
   *
   * Not Math.round: it rounds -2.5 to -2, which is half-up towards positive
   * infinity rather than away from zero, and Python's ROUND_HALF_UP is the
   * latter. On a column containing refunds the two disagree by a cent. */
  function divHalfUp(n, d) {
    if (n >= 0) { return Math.floor((n * 2 + d) / (2 * d)); }
    return -Math.floor((-n * 2 + d) / (2 * d));
  }

  /* A rate as written in the file — "1.6043" — as an exact integer and its
   * scale, so the multiplication never touches a float. */
  function rateParts(text) {
    var s = String(text).trim();
    var dot = s.indexOf('.');
    var scale = dot < 0 ? 0 : s.length - dot - 1;
    var digits = s.replace('.', '');
    return { num: parseInt(digits, 10), scale: scale,
             pow: Math.pow(10, scale) };
  }

  /* money.convert: cents in EUR at `rate`, as cents in the target. */
  function convert(cents, rate) {
    var parts = rateParts(rate);
    if (!(parts.num > 0)) { throw new RateError('implausible rate ' + rate); }
    return divHalfUp(cents * parts.num, parts.pow);
  }

  /* money.parse, rule for rule.
   *
   * The last separator present wins as the decimal point, which is what makes
   * "1.234,56" and "1,234.56" both come out right — a German bank CSV and a
   * Canadian one import without a flag, and getting it wrong is a
   * factor-of-100 error rather than a rounding one.
   *
   * A longer fraction is *truncated*, not refused: "1.2345" is 123 cents. The
   * first version of this file refused it, because that is what the
   * screenshot parser in docs/capture/rules.js does — and rightly, since a
   * figure with four decimals on a receipt is probably an exchange rate
   * rather than a price. Here the text is typed or comes from a CSV, so the
   * two parsers genuinely differ, and the shared fixture caught the copy. */
  function parseMoney(text) {
    var raw = String(text).trim();
    if (!raw) { return null; }

    /* Accounting negatives: (12,34) is -12.34 */
    var negative = raw.charAt(0) === '-' ||
      (raw.charAt(0) === '(' && raw.charAt(raw.length - 1) === ')');

    var cleaned = raw.replace(/[^\d,.\-+]/g, '').replace(/^[+-]+/, '');
    if (!/\d/.test(cleaned)) { return null; }

    var cut = Math.max(cleaned.lastIndexOf(','), cleaned.lastIndexOf('.'));
    var whole, fraction;

    if (cut < 0) {
      whole = cleaned;
      fraction = '';
    } else {
      var tail = cleaned.slice(cut + 1);
      var before = cleaned.slice(0, cut);
      var others = (before.match(/[,.]/g) || []).length;
      if (tail.length === 3 && others === 0) {
        /* "1,234" is a thousand, not 1.234. Two is the only unambiguous
         * decimal length for these currencies. */
        whole = cleaned.replace(/[,.]/g, '');
        fraction = '';
      } else {
        whole = before.replace(/[,.]/g, '');
        fraction = tail;
      }
    }

    if (whole !== '' && !/^\d+$/.test(whole)) { return null; }
    if (fraction && !/^\d+$/.test(fraction)) { return null; }

    var minor = parseInt(whole || '0', 10) * Math.pow(10, MINOR_UNITS) +
      parseInt(fraction ? (fraction + '00').slice(0, MINOR_UNITS) : '00', 10);
    return negative ? -minor : minor;
  }

  var SYMBOL = { EUR: '€', CAD: 'CA$', USD: 'US$', GBP: '£' };

  /* The machine-readable form: no symbol, no grouping. core/money.py has the same
   * pair for the same reason -- the grouped one is for a person reading a
   * column, and putting it in a CSV writes "1,234.56" into a comma-separated
   * file. The export did exactly that until a purchase over a thousand went
   * through it. */
  function plain(cents) {
    var sign = cents < 0 ? '-' : '';
    var n = Math.abs(cents);
    return sign + Math.floor(n / 100) + '.' + String(n % 100).padStart(2, '0');
  }

  function format(cents, currency, symbol) {
    var sign = cents < 0 ? '-' : '';
    var n = Math.abs(cents);
    var whole = String(Math.floor(n / 100));
    var rest = String(n % 100).padStart(2, '0');
    var grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    var mark = symbol === false ? '' : (SYMBOL[currency] || '');
    return sign + mark + grouped + '.' + rest;
  }

  /* -------------------------------------------------------------- rates */

  function loadRates(text) {
    rates = {};
    var lines = text.trim().split(/\r?\n/);
    var header = lines[0].split(',');       // date,CAD,USD
    columns = header.slice(1).map(function (name) { return name.trim(); })
                    .filter(function (name) { return name; });
    for (var i = 1; i < lines.length; i++) {
      var cells = lines[i].split(',');
      if (cells.length < 2) { continue; }
      var row = {};
      for (var c = 1; c < header.length; c++) {
        if (cells[c]) { row[header[c].trim()] = cells[c].trim(); }
      }
      rates[cells[0]] = row;
    }
    newest = Object.keys(rates).sort().pop();
    return { days: Object.keys(rates).length, newest: newest };
  }

  function loaded() { return rates !== null; }
  function newestDate() { return newest; }
  function oldestDate() { return Object.keys(rates).sort()[0]; }

  /* fxrates.rate: [rate, the date it is from]. */
  function rate(on, currency) {
    if (currency === BASE) { return { rate: '1', from: on }; }
    if (!rates) { throw new RateError('no rate cache loaded'); }
    /* The honest question is whether the loaded table has this currency,
     * not whether it is on a list written here. For the app's own file the
     * two answers are the same; the pound wallet loads date,CAD,GBP into
     * this same reader. A typo is still refused outright rather than
     * walking back ten days for a column that does not exist. */
    if (columns.indexOf(currency) < 0) {
      throw new RateError('not a currency this app converts to: ' + currency);
    }

    /* Rule 3: refused only if a rate for it could still arrive. */
    if (on > newest && weekday(on) < 5) {
      throw new RateError(on + ' is later than the newest rate (' + newest +
                          '); check the transaction date');
    }

    for (var back = 0; back <= MAX_LOOKBACK_DAYS; back++) {
      var day = minusDays(on, back);
      var found = rates[day];
      if (found && found[currency]) {
        return { rate: found[currency], from: day };
      }
    }
    throw new RateError('no ' + currency + ' rate within ' +
                        MAX_LOOKBACK_DAYS + ' days before ' + on);
  }

  /* fxrates.convert: the one call the ledger needs. */
  function convertOn(cents, on, currency) {
    var found = rate(on, currency);
    return {
      minor: convert(cents, found.rate),
      rate: found.rate,
      rateDate: found.from,
      lagDays: Math.round((utc(on) - utc(found.from)) / 86400000)
    };
  }

  function convertAll(cents, on) {
    var out = {};
    TARGETS.forEach(function (currency) {
      try {
        out[currency] = convertOn(cents, on, currency);
      } catch (bad) {
        out[currency] = { error: bad.message };
      }
    });
    return out;
  }

  /* fxcost.estimate: what a euro purchase will bill on a card charging
   * `feeBp`. The fee applies to the *converted* amount, which is how the
   * card states it and how the measured statements bear out — fee-then-
   * convert agrees on a coffee and drifts on a flight. */
  function estimate(baseMinor, rateText, feeBp) {
    baseMinor = Math.abs(baseMinor);
    feeBp = parseInt(feeBp, 10);
    if (!(feeBp >= 0)) { throw new RateError('a card fee cannot be negative'); }

    var converted = convert(baseMinor, rateText);
    var fee = divHalfUp(converted * feeBp, BASIS_POINTS);
    return {
      baseMinor: baseMinor,
      convertedMinor: converted,
      feeBp: feeBp,
      feeMinor: fee,
      totalMinor: converted + fee,
      /* The all-in rate, which is what makes the fee real: it is the figure
       * to hold against the mid-market rate you looked up. */
      effectiveRate: baseMinor
        ? (divHalfUp((converted + fee) * 1000000, baseMinor) / 1000000)
            .toFixed(6)
        : null
    };
  }

  return {
    BASE: BASE, TARGETS: TARGETS, MAX_LOOKBACK_DAYS: MAX_LOOKBACK_DAYS,
    loadRates: loadRates, loaded: loaded,
    newestDate: newestDate, oldestDate: oldestDate,
    rate: rate, convert: convert, convertOn: convertOn, convertAll: convertAll,
    estimate: estimate, parseMoney: parseMoney, format: format,
    plain: plain, divHalfUp: divHalfUp, columns: function () { return columns.slice(); },
    RateError: RateError
  };
}());
