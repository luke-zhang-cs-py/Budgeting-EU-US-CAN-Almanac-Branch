/* gbp.js — Canadian dollars into pounds at the CIBC rate, in the browser.
 *
 * The port of fx/pounds.py, and the third deliberate second implementation in
 * this project after docs/capture/rules.js and docs/app/fx.js. It is not
 * trusted to stay in step: cases.json beside this file holds the shared
 * cases, tests/test_pounds.py asserts fx/pounds.py produces those answers and
 * that a headless browser running this file produces the same ones.
 *
 * The rate lookup is not duplicated. fx.js already walks back to the last
 * published business day and refuses a future date, and it reads whatever
 * columns its CSV carries — so this page hands it a date,CAD,GBP file and
 * asks it for both legs. What is new here, and all that is new, is the
 * cross rate, CIBC's markup inverted, and the limit.
 *
 * BigInt rather than Number, which is the one place this file departs from
 * the style of fx.js. The conversion is one exact ratio of integers (see
 * fx/pounds.py) and its numerator passes 10^20 on a four-figure amount —
 * comfortably past Number.MAX_SAFE_INTEGER, where the answer would start
 * being off by a penny in ways no test date would reliably catch. Python
 * has arbitrary-precision integers for free; this is the cost of matching
 * it exactly rather than approximately.
 */

'use strict';

var GBP = (function () {

  /* pounds.LIMIT_MINOR — the ceiling, in pence. */
  var LIMIT_MINOR = 100000;

  /* pounds.CIBC_FEE_BP — CIBC's published foreign conversion markup. */
  var CIBC_FEE_BP = 250;

  var BASIS_POINTS = 10000n;    // fxcost.BASIS_POINTS
  var RATE_PLACES = 6;          // pounds.RATE_PLACES

  var FROM = 'CAD';
  var TO = 'GBP';

  function PoundsError(message) {
    this.name = 'PoundsError';
    this.message = message;
  }
  PoundsError.prototype = new Error();

  /* ---------------------------------------------------------- integers */

  /* pounds._parts: "1.6064" -> { num: 16064n, scale: 4 }. */
  function parts(value) {
    var text = String(value).trim();
    if (!/^\d+(\.\d+)?$/.test(text)) {
      throw new PoundsError('implausible rate ' + value);
    }
    var dot = text.indexOf('.');
    if (dot < 0) { return { num: BigInt(text), scale: 0 }; }
    var scale = text.length - dot - 1;
    return { num: BigInt(text.slice(0, dot) + text.slice(dot + 1)),
             scale: scale };
  }

  function pow10(n) { return 10n ** BigInt(n); }

  /* pounds.div_half_up, on BigInt. Half away from zero, as core/money.py rounds. */
  function divHalfUp(numerator, denominator) {
    if (denominator <= 0n) {
      throw new PoundsError('implausible divisor ' + denominator);
    }
    if (numerator >= 0n) {
      return (numerator * 2n + denominator) / (denominator * 2n);
    }
    return -((-numerator * 2n + denominator) / (denominator * 2n));
  }

  /* A BigInt of minor units scaled by 10^places, as a fixed-point string.
   * Python gets this from Decimal.scaleb().quantize(); the digits are the
   * same, so the text has to be too — "1.000000", not "1". */
  function fixed(value, places) {
    var sign = value < 0n ? '-' : '';
    var digits = (value < 0n ? -value : value).toString()
                   .padStart(places + 1, '0');
    return sign + digits.slice(0, digits.length - places) + '.' +
           digits.slice(digits.length - places);
  }

  /* ------------------------------------------------------------- rates */

  /* pounds.legs: both euro legs, from one published day.
   *
   * fx.js walks each back independently. They land together on every day
   * the ECB has published both, which is every day in the shipped file —
   * but a file with one leg missing would otherwise produce a cross rate
   * that was never quoted, so the dates are compared rather than assumed. */
  function legs(on) {
    var cad = FX.rate(on, FROM);
    var gbp = FX.rate(on, TO);
    if (cad.from !== gbp.from) {
      throw new PoundsError('the CAD rate is from ' + cad.from +
                            ' and the GBP rate from ' + gbp.from +
                            '; a cross rate needs both legs from one day');
    }
    return { cad: cad.rate, gbp: gbp.rate, from: cad.from };
  }

  /* pounds.cross: CAD per GBP, for showing. Nothing converts with it. */
  function cross(cadRate, gbpRate, places) {
    places = places === undefined ? RATE_PLACES : places;
    var c = parts(cadRate), g = parts(gbpRate);
    return fixed(divHalfUp(c.num * pow10(g.scale) * pow10(places),
                           g.num * pow10(c.scale)), places);
  }

  /* pounds.to_pounds. The fee divides rather than multiplies; fx/pounds.py's
   * docstring is the argument for why, and test_pounds.py round-trips every
   * case through fxcost.estimate to keep the direction honest. */
  function toPounds(cadMinor, cadRate, gbpRate, feeBp) {
    cadMinor = Math.abs(parseInt(cadMinor, 10));
    feeBp = feeBp === undefined ? CIBC_FEE_BP : parseInt(feeBp, 10);
    if (!(cadMinor >= 0)) { throw new PoundsError('not an amount'); }
    if (!(feeBp >= 0)) {
      throw new PoundsError('a card fee cannot be negative: ' + feeBp);
    }

    var c = parts(cadRate), g = parts(gbpRate);
    if (c.num <= 0n || g.num <= 0n) {
      throw new PoundsError('implausible rates CAD ' + cadRate +
                            ', GBP ' + gbpRate);
    }

    var numerator = BigInt(cadMinor) * g.num * pow10(c.scale);
    var denominator = c.num * pow10(g.scale);

    var reference = divHalfUp(numerator, denominator);
    var pounds = divHalfUp(numerator * BASIS_POINTS,
                           denominator * (BASIS_POINTS + BigInt(feeBp)));

    return {
      cadMinor: cadMinor,
      referenceMinor: Number(reference),
      poundsMinor: Number(pounds),
      feeMinor: Number(reference - pounds),
      feeBp: feeBp,
      cross: cross(cadRate, gbpRate),
      effectiveRate: cadMinor
        ? fixed(divHalfUp(pounds * pow10(6), BigInt(cadMinor)), 6)
        : null
    };
  }

  /* pounds.from_pounds: the dollars needed to end up with poundsMinor.
   *
   * toPounds solved for the other unknown, and the same exact ratio upside
   * down — so "how much can I still convert" is computed rather than
   * estimated by scaling the last answer. */
  function fromPounds(poundsMinor, cadRate, gbpRate, feeBp) {
    poundsMinor = Math.abs(parseInt(poundsMinor, 10));
    feeBp = feeBp === undefined ? CIBC_FEE_BP : parseInt(feeBp, 10);
    if (!(poundsMinor >= 0)) { throw new PoundsError('not an amount'); }
    if (!(feeBp >= 0)) {
      throw new PoundsError('a card fee cannot be negative: ' + feeBp);
    }

    var c = parts(cadRate), g = parts(gbpRate);
    if (c.num <= 0n || g.num <= 0n) {
      throw new PoundsError('implausible rates CAD ' + cadRate +
                            ', GBP ' + gbpRate);
    }

    return Number(divHalfUp(
      BigInt(poundsMinor) * c.num * pow10(g.scale)
        * (BASIS_POINTS + BigInt(feeBp)),
      g.num * pow10(c.scale) * BASIS_POINTS));
  }

  /* pounds.convert_on. */
  function convertOn(cadMinor, on, feeBp) {
    var pair = legs(on);
    var out = toPounds(cadMinor, pair.cad, pair.gbp, feeBp);
    out.on = on;
    out.rateDate = pair.from;
    out.lagDays = Math.round(
      (Date.parse(on + 'T00:00:00Z') - Date.parse(pair.from + 'T00:00:00Z'))
      / 86400000);
    return out;
  }

  /* ------------------------------------------------------------- limit */

  function remaining(spentMinor, limitMinor) {
    limitMinor = limitMinor === undefined ? LIMIT_MINOR : limitMinor;
    return limitMinor - spentMinor;
  }

  /* Exactly on the limit fits: 1,000.00 of a 1,000 budget is spent, not
   * overspent. */
  function fits(spentMinor, poundsMinor, limitMinor) {
    limitMinor = limitMinor === undefined ? LIMIT_MINOR : limitMinor;
    return spentMinor + poundsMinor <= limitMinor;
  }

  return {
    LIMIT_MINOR: LIMIT_MINOR, CIBC_FEE_BP: CIBC_FEE_BP,
    FROM: FROM, TO: TO, RATE_PLACES: RATE_PLACES,
    parts: parts, divHalfUp: divHalfUp, fixed: fixed,
    legs: legs, cross: cross, toPounds: toPounds, fromPounds: fromPounds,
    convertOn: convertOn,
    remaining: remaining, fits: fits,
    PoundsError: PoundsError
  };
}());
