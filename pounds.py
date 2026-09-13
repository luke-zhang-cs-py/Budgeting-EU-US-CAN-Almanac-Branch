"""Canadian dollars into pounds at the CIBC rate, against a 1,000 limit.

The wallet proper converts euros into CAD and USD. This is the other
direction and a different currency: you hold Canadian dollars in a CIBC
account, you are spending in Britain, and the question is how many pounds a
Canadian dollar actually becomes once CIBC has taken its cut -- with a hard
1,000 ceiling so the running total means something.

**The cross rate.** The ECB publishes against the euro, so there is no
CAD->GBP line to look up. There is EUR->CAD and EUR->GBP on the same day, and
the euro cancels:

    CAD per GBP = (EUR->CAD) / (EUR->GBP)

Both legs come from the same published day, which matters: mixing a Tuesday
CAD rate with a Monday GBP rate invents a cross rate that was never quoted.
`fxrates.rate` walks each leg back to the last business day independently, so
this module asks for the pair and refuses if they land on different days.

**The fee, and why it divides rather than multiplies.** fxcost.estimate is
the forward direction -- you buy something priced in a foreign currency and
CIBC converts, then adds 2.5% to the Canadian figure. Going the other way is
the same rule solved for the other unknown. To end up holding P pounds you
are charged

    C = P x (CAD per GBP) x 1.025

so a fixed C buys

    P = C / ((CAD per GBP) x 1.025)

Multiplying by 0.975 instead is the tempting shortcut and it is wrong: it
answers "what is 2.5% off my pounds", not "what does a 2.5% markup on the
Canadian side leave me". Converting CA$1,000 at 1.87179 the shortcut says
520.89 and the right answer is 521.22 -- 33 pence, in your favour, and it
grows with the amount. test_pounds.py pins the direction by round-tripping
every case through fxcost.estimate: converting C dollars into P pounds is
only right if estimating P pounds on a 2.5% card bills C dollars back.

**Why there is no intermediate rate.** The obvious shape is to divide the
legs, get a CAD-per-GBP decimal, and convert with it. That number does not
terminate, so it has to be rounded somewhere, and then two implementations
have to round it to the same place before they can agree on a penny -- and
the browser cannot hold 34 significant digits in a double anyway. So the
legs stay whole and the conversion is one exact ratio of integers, rounded
once at the end:

    pence = cents x GBP_num x 10^CAD_scale x 10000
            ---------------------------------------
            CAD_num x 10^GBP_scale x (10000 + fee)

Every term is an integer, so Python and gbp.js compute the same value rather
than the same value to within a rounding mode. The cross rate is still shown,
because a reader wants to see it, but nothing is computed from it.
"""
import datetime as dt
from decimal import Decimal, ROUND_HALF_UP

import fxcost
import fxrates
import money

# The ceiling, in pence. A budget, not a bank balance: the wallet refuses to
# record a conversion that would carry the running total past it, because a
# limit that only prints a warning is not a limit.
LIMIT_MINOR = 100000

# CIBC's published foreign currency conversion markup. fxcost calls this
# TYPICAL_CARD_FEE_BP; naming it again here is deliberate -- this file is
# about one bank's one rate, and a shared "typical" drifting would silently
# restate what CIBC charges.
CIBC_FEE_BP = 250

FROM = "CAD"
TO = "GBP"

# How many decimal places the displayed cross rate carries. Display only --
# see the module docstring.
RATE_PLACES = 6


class PoundsError(Exception):
    """A conversion that cannot be stated honestly."""


def _as_date(on):
    """A date from either a date or an ISO string.

    fxrates.rate takes a date object; the browser side and the JSON fixture
    both speak ISO strings, and the pair have to agree case for case.
    """
    if isinstance(on, dt.datetime):
        return on.date()
    if isinstance(on, dt.date):
        return on
    return dt.datetime.strptime(str(on).strip(), "%Y-%m-%d").date()


def _parts(value):
    """(integer numerator, decimal places) for an exact decimal.

    "1.6064" -> (16064, 4). The pair is what lets the conversion stay in
    integers; see the module docstring.
    """
    number = Decimal(str(value))
    exponent = number.as_tuple().exponent
    if not isinstance(exponent, int):        # NaN or Infinity
        raise money.MoneyError(f"implausible rate {value}")
    if exponent >= 0:
        return int(number), 0
    return int(number.scaleb(-exponent)), -exponent


def div_half_up(numerator, denominator):
    """Integer division rounding half away from zero.

    money.convert's rounding, done on integers instead of through Decimal,
    because the browser side has to reproduce it exactly and this is the form
    that ports. Half-up rather than banker's for money.py's reason: it is
    what a person gets doing it by hand.
    """
    if denominator <= 0:
        raise money.MoneyError(f"implausible divisor {denominator}")
    if numerator >= 0:
        return (numerator * 2 + denominator) // (denominator * 2)
    return -((-numerator * 2 + denominator) // (denominator * 2))


def legs(on, directory=None):
    """(EUR->CAD, EUR->GBP, the date both came from) for `on`.

    Both legs must be the same published day. They almost always are -- the
    ECB publishes every currency together -- but "almost always" is how a
    fabricated cross rate gets into a total, so it is checked rather than
    assumed.
    """
    on = _as_date(on)
    cad, cad_from = fxrates.rate(on, FROM, directory=directory)
    gbp, gbp_from = fxrates.rate(on, TO, directory=directory)
    if cad_from != gbp_from:
        raise PoundsError(
            f"the CAD rate is from {cad_from} and the GBP rate from "
            f"{gbp_from}; a cross rate needs both legs from one day")
    if cad <= 0 or gbp <= 0:
        raise PoundsError(f"implausible rates CAD {cad}, GBP {gbp}")
    return cad, gbp, cad_from


def cross(cad_rate, gbp_rate, places=RATE_PLACES):
    """CAD per GBP, as a string, for showing. Nothing converts with it."""
    cad_num, cad_scale = _parts(cad_rate)
    gbp_num, gbp_scale = _parts(gbp_rate)
    scaled = div_half_up(cad_num * 10 ** gbp_scale * 10 ** places,
                         gbp_num * 10 ** cad_scale)
    return str(Decimal(scaled).scaleb(-places).quantize(
        Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def to_pounds(cad_minor, cad_rate, gbp_rate, fee_bp=CIBC_FEE_BP):
    """`cad_minor` Canadian cents as pence, via the euro, after CIBC's cut.

    Returns the reference figure, what CIBC actually leaves you, and the
    difference -- all three, because the fee is the whole point of the file
    and a single net number hides it.
    """
    cad_minor = abs(int(cad_minor))
    fee_bp = int(fee_bp)
    if fee_bp < 0:
        raise money.MoneyError(f"a card fee cannot be negative: {fee_bp}")

    cad_num, cad_scale = _parts(cad_rate)
    gbp_num, gbp_scale = _parts(gbp_rate)
    if cad_num <= 0 or gbp_num <= 0:
        raise money.MoneyError(
            f"implausible rates CAD {cad_rate}, GBP {gbp_rate}")

    # One exact ratio, rounded once. See the module docstring.
    numerator = cad_minor * gbp_num * 10 ** cad_scale
    denominator = cad_num * 10 ** gbp_scale

    reference = div_half_up(numerator, denominator)
    pounds = div_half_up(numerator * fxcost.BASIS_POINTS,
                         denominator * (fxcost.BASIS_POINTS + fee_bp))

    return {
        "cad_minor": cad_minor,
        "reference_minor": reference,
        "pounds_minor": pounds,
        "fee_minor": reference - pounds,
        "fee_bp": fee_bp,
        "cross": cross(cad_rate, gbp_rate),
        # Pence per Canadian cent, all in. The figure to hold against the
        # mid-market rate you looked up.
        "effective_rate": (str(Decimal(div_half_up(pounds * 10 ** 6,
                                                   cad_minor))
                               .scaleb(-6).quantize(Decimal("0.000001")))
                           if cad_minor else None),
    }


def from_pounds(pounds_minor, cad_rate, gbp_rate, fee_bp=CIBC_FEE_BP):
    """The dollars needed to end up holding `pounds_minor` pence.

    to_pounds solved for the other unknown, and the same exact ratio upside
    down -- so "how much can I still convert" is computed rather than
    estimated by scaling the last answer. Rounding means it is a left
    inverse only to within a penny, which test_pounds.py states as the
    property it actually has rather than claiming an exact one.
    """
    pounds_minor = abs(int(pounds_minor))
    fee_bp = int(fee_bp)
    if fee_bp < 0:
        raise money.MoneyError(f"a card fee cannot be negative: {fee_bp}")

    cad_num, cad_scale = _parts(cad_rate)
    gbp_num, gbp_scale = _parts(gbp_rate)
    if cad_num <= 0 or gbp_num <= 0:
        raise money.MoneyError(
            f"implausible rates CAD {cad_rate}, GBP {gbp_rate}")

    return div_half_up(
        pounds_minor * cad_num * 10 ** gbp_scale
        * (fxcost.BASIS_POINTS + fee_bp),
        gbp_num * 10 ** cad_scale * fxcost.BASIS_POINTS)


def convert_on(cad_minor, on, directory=None, fee_bp=CIBC_FEE_BP):
    """to_pounds for a date, with the rate date it actually used."""
    cad, gbp, rate_date = legs(on, directory=directory)
    out = to_pounds(cad_minor, cad, gbp, fee_bp)
    out["on"] = _as_date(on).isoformat()
    out["rate_date"] = rate_date.isoformat()
    out["lag_days"] = (_as_date(on) - rate_date).days
    return out


def remaining(spent_minor, limit_minor=LIMIT_MINOR):
    """What is left of the limit. Negative is possible only if a caller
    ignored `fits`; it is reported rather than clamped, because a total that
    silently stops at zero is a total that lies."""
    return int(limit_minor) - int(spent_minor)


def fits(spent_minor, pounds_minor, limit_minor=LIMIT_MINOR):
    """Whether adding `pounds_minor` keeps the running total inside the
    limit. Exactly on the limit fits -- 1,000.00 of a 1,000 budget is spent,
    not overspent."""
    return int(spent_minor) + int(pounds_minor) <= int(limit_minor)
