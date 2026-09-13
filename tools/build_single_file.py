"""Build the wallet as one self-contained HTML file.

    python tools/build_single_file.py --out ../wallet.html

One file, opened by double-clicking it. No server, no folder of assets, no
network of any kind: the stylesheet, both scripts and the whole ECB rate
history are inlined, and the result is a file you can copy to a USB stick or
email to yourself and it still converts a purchase at the right historical
rate.

Inlining the rates is not an optimisation. A file opened from disk has a
`file://` origin, where `fetch` is refused outright -- so the served version
fetches its rates and the single file carries them, and `boot()` in app.js
handles both. There is one app.js either way; a second copy of the page
would be a second copy of every rule in it.

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
  The wallet, as one file.

  Built by tools/build_single_file.py from docs/app/ -- edit the sources
  there and rebuild rather than editing this, which is generated.

  Everything is inside this file: the stylesheet, the code, and %s business
  days of European Central Bank reference rates from %s to %s. It makes no
  network requests and its Content-Security-Policy forbids them, so nothing
  you record here can leave this file. Purchases are kept in the browser's
  localStorage for whatever page opened it.

  The rates are a snapshot. A purchase dated after %s on a weekday is
  refused rather than converted at a stale rate -- the same refusal the
  server version makes, for the same reason.
-->
"""


def read(*parts):
    with io.open(os.path.join(*parts), encoding="utf-8") as handle:
        return handle.read()


def build():
    page = read(APP, "index.html")
    rates = read(APP, "fx_rates.csv")

    dates = sorted(line.split(",")[0] for line in rates.splitlines()[1:]
                   if line.strip())
    span = (str(len(dates)), dates[0], dates[-1], dates[-1])

    # The policy, rewritten for a file with nothing to fetch.
    page = re.sub(r'<meta http-equiv="Content-Security-Policy" content="[^"]*">',
                  '<meta http-equiv="Content-Security-Policy" content="%s">'
                  % POLICY, page, count=1)
    assert POLICY in page, "the policy meta tag was not where it was expected"

    # The stylesheet, inline.
    style = '<style>\n%s</style>' % read(APP, "style.css")
    page = page.replace('<link rel="stylesheet" href="style.css">', style, 1)
    assert "<style>" in page, "the stylesheet link was not replaced"

    # The rates and both scripts, inline and in the order the page loads them.
    scripts = (
        '<script>\n/* The ECB reference rates, inlined -- see the note at the '
        'top of this file. */\nvar RATES_CSV = %s;\n</script>\n'
        '<script>\n%s</script>\n'
        '<script>\n%s</script>'
        % (json.dumps(rates), read(APP, "fx.js"), read(APP, "app.js"))
    )
    page = page.replace('<script src="fx.js"></script>\n'
                        '<script src="app.js"></script>', scripts, 1)
    assert 'src="fx.js"' not in page and 'src="app.js"' not in page, (
        "the script tags were not replaced, so the file is not self-contained")

    # Links to pages that will not be beside this file. A standalone file
    # copied to a USB stick has no ../ to speak of, so the words stay and the
    # anchors go -- a dead link is worse than plain text, and the check at
    # the end of main() is what stopped this shipping as one.
    page = page.replace(
        '<span><a href="../">About the wallet app &rarr;</a></span>',
        '<span>One file. Nothing here reaches the network.</span>', 1)
    page = re.sub(r'<a href="\.\./capture/">([^<]*)</a>', r"\1", page)

    page = page.replace("<!doctype html>", "<!doctype html>\n" + BANNER % span, 1)
    return page, span


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=os.path.join(ROOT, "wallet.html"),
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
    print("  %.0f KB, %s rate days, %s to %s, no external references"
          % (size / 1024, span[0], span[1], span[2]))


if __name__ == "__main__":
    main()
