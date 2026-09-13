"""Build the pound wallet as one self-contained HTML file.

    python tools/build_pounds_file.py --out ../wallet-pounds.html

The same shape as tools/build_single_file.py, for the same reasons, over a
different set of sources: two stylesheets instead of one, three scripts
instead of two, and a rate file carrying both euro legs instead of CAD and
USD. The two builders are kept apart rather than generalised behind flags --
they differ in every one of those particulars, and a parameterised builder
that got one of them wrong would produce a file that still opened.

One file, opened by double-clicking it. No server, no folder of assets, no
network of any kind: both stylesheets, all three scripts and the whole ECB
rate history are inlined, and the result converts a Canadian dollar into
pounds at the rate published on the day it was spent.

Inlining the rates is not an optimisation. A file opened from disk has a
`file://` origin, where `fetch` is refused outright -- so the served version
fetches its rates and this one carries them, and `boot()` in app.js handles
both shapes. There is one app.js either way.

The Content-Security-Policy changes, and on balance it gets stronger. The
served page allows `connect-src 'self'` so it can fetch the rate file. This
one needs no network at all, so nothing is allowed: `default-src 'none'`
with no `connect-src` means the file *cannot* make a request, which is a
better guarantee than a promise not to. The cost is `script-src
'unsafe-inline'`, since every script is now inline -- worth naming rather
than glossing, though with no network reachable there is nowhere for an
injected script to send anything, and the only untrusted text on the page
(descriptions, categories) goes through esc().
"""
import argparse
import io
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "docs", "app")
PAGE = os.path.join(ROOT, "standalone", "pounds")

POLICY = (
    "default-src 'none'; "
    "script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; "
    "img-src data:; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)

BANNER = """<!--
  The pound wallet, as one file.

  Built by tools/build_pounds_file.py from standalone/pounds/ -- edit the
  sources there and rebuild rather than editing this, which is generated.

  Canadian dollars into pounds, against a %s limit. There is no published
  CAD-GBP rate, so it crosses two that are: the European Central Bank's euro
  reference rates for both currencies, both legs from the same day. CIBC's
  2.5%% conversion markup is charged on the Canadian side, so it is divided
  out rather than taken off the pounds -- see pounds.py for why those are
  different numbers.

  Everything is inside this file: the stylesheets, the code, and %s business
  days of rates from %s to %s. It makes no network requests and its
  Content-Security-Policy forbids them, so nothing you record here can leave
  this file. Conversions are kept in the browser's localStorage for whatever
  page opened it, under "wallet.pounds.v1" -- its own key, so it does not mix
  with the euro wallet's ledger.

  The rates are a snapshot. A conversion dated after %s on a weekday is
  refused rather than converted at a stale rate.
-->
"""


def read(*parts):
    with io.open(os.path.join(*parts), encoding="utf-8") as handle:
        return handle.read()


def build():
    page = read(PAGE, "index.html")
    rates = read(PAGE, "fx_rates.csv")

    dates = sorted(line.split(",")[0] for line in rates.splitlines()[1:]
                   if line.strip())

    # The limit, read from the source rather than typed here, so the banner
    # cannot end up describing a different one from the code below it.
    found = re.search(r"var LIMIT_MINOR = (\d+);", read(PAGE, "gbp.js"))
    if not found:
        raise SystemExit("gbp.js has no LIMIT_MINOR to quote in the banner")
    limit = "£{:,}".format(int(found.group(1)) // 100)

    span = (limit, str(len(dates)), dates[0], dates[-1], dates[-1])

    # The policy, rewritten for a file with nothing to fetch.
    page = re.sub(r'<meta http-equiv="Content-Security-Policy" content="[^"]*">',
                  '<meta http-equiv="Content-Security-Policy" content="%s">'
                  % POLICY, page, count=1)
    assert POLICY in page, "the policy meta tag was not where it was expected"

    # Both stylesheets, inline and in the order the page loads them -- the
    # wallet's own first, then the handful of rules this page adds on top.
    page = page.replace(
        '<link rel="stylesheet" href="../../docs/app/style.css">\n'
        '<link rel="stylesheet" href="limit.css">',
        '<style>\n%s</style>\n<style>\n%s</style>'
        % (read(APP, "style.css"), read(PAGE, "limit.css")), 1)
    assert "<style>" in page, "the stylesheet links were not replaced"

    # The rates and all three scripts, in load order.
    scripts = (
        '<script>\n/* The ECB reference rates, inlined -- see the note at the '
        'top of this file. */\nvar RATES_CSV = %s;\n</script>\n'
        '<script>\n%s</script>\n'
        '<script>\n%s</script>\n'
        '<script>\n%s</script>'
        % (json.dumps(rates), read(APP, "fx.js"), read(PAGE, "gbp.js"),
           read(PAGE, "app.js"))
    )
    page = page.replace('<script src="../../docs/app/fx.js"></script>\n'
                        '<script src="gbp.js"></script>\n'
                        '<script src="app.js"></script>', scripts, 1)
    assert 'src="gbp.js"' not in page and 'src="app.js"' not in page, (
        "the script tags were not replaced, so the file is not self-contained")

    # There is no footer link to strip. The euro builder rewrites one because
    # its source page is served by Pages and wants a way back; this source is
    # not published at all, so it carries no outbound links to begin with.
    # The check at the end of main() is what keeps that true.

    page = page.replace("<!doctype html>", "<!doctype html>\n" + BANNER % span, 1)
    return page, span


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out",
                        default=os.path.join(ROOT, "wallet-pounds.html"),
                        help="where to write the file")
    args = parser.parse_args()

    page, span = build()

    # A file that still points at something beside it is not self-contained,
    # and the failure only shows when somebody opens it somewhere else.
    loose = re.findall(r'(?:src|href)="(?!#|https?:|mailto:|data:)([^"]+)"',
                       page)
    if loose:
        raise SystemExit("these still point outside the file: %s" % loose)

    with io.open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(page)

    size = os.path.getsize(args.out)
    print("  %s" % args.out)
    print("  %.0f KB, %s limit, %s rate days, %s to %s, no external references"
          % (size / 1024, span[0], span[1], span[2], span[3]))


if __name__ == "__main__":
    main()
