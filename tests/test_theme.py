"""The colour themes: the tokens, their contrast, the toggle, and no-JS.

Every page here opens dark, from `class="theme-dark"` written into `<html>`,
so it is fully themed before -- and without -- any JavaScript. A toggle
steps through THEMES, saves the choice under the key 'theme' (shared with
every other page on the GitHub Pages origin, on purpose), and an unknown
saved name falls back to dark.

The colour lives in one block per theme and nowhere else, so the checks here
are mostly about those blocks:

  * every theme in THEMES has a block, and every block sets the same tokens
    as the default -- a theme missing a token silently inherits nothing and
    draws that thing in the browser's default colour;
  * every foreground/background pairing the pages actually use meets WCAG AA
    in every theme, computed from the tokens rather than from a rendered page,
    because the pairs that matter most (an error notice, a hovered row, an
    over-budget figure) are the ones a scan of a freshly loaded page never
    sees;
  * no component rule carries a raw colour, which is what keeps the first two
    true;
  * the app's three stylesheets carry the same palette, and the three copies
    of theme.js are the same file -- they are served separately, so they are
    copies, and copies are checked rather than kept in step by eye.

Then, in a real browser: a page with scripts disabled is dark, the toggle
switches and remembers, an unknown saved theme falls back, and a scan of
every visible text node passes in both themes with each notice state shown.
"""
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _at(*parts):
    return os.path.join(ROOT, *parts)


def _read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


CONTRACT = {
    "--color-bg", "--color-surface", "--color-surface-subtle",
    "--color-border", "--color-border-strong", "--color-text-primary",
    "--color-text-muted", "--color-text-subtle", "--color-accent",
    "--color-accent-hover", "--color-accent-subtle", "--color-text-on-accent",
    "--color-danger",
}

# The wallet's own palette: the Flask app, the static wallet (which the pound
# wallet loads too) and the capture page.
APP_SHEETS = {
    "app": _at("static", "css", "style.css"),
    "static wallet": _at("docs", "app", "style.css"),
    "capture": _at("docs", "capture", "style.css"),
}
WRITEUP = _at("docs", "index.html")
THEME_JS = [_at("docs", "app", "theme.js"), _at("docs", "capture", "theme.js"),
            _at("static", "js", "theme.js")]

# Every rule that draws colour, including the ones with no theme blocks of
# their own: limit.css sits on the static wallet's tokens, login.html's few
# rules on the app's.
ALL_STYLE = dict(APP_SHEETS, **{
    "pound wallet": _at("standalone", "pounds", "limit.css"),
    "login": _at("templates", "login.html"),
    "write-up": WRITEUP,
})

# The pages, as a person opens them.
PAGES = [_at("docs", "app", "index.html"), _at("docs", "capture", "index.html"),
         _at("docs", "index.html"), _at("standalone", "pounds", "index.html"),
         _at("templates", "index.html"), _at("templates", "simple.html"),
         _at("templates", "login.html")]

SURFACES = ("bg", "surface", "surface-subtle")

# fg on bg, as the rules use them. Text needs 4.5:1; every size on these
# pages is under the 24px large-text line except the totals, and they are
# held to 4.5 anyway.
APP_TEXT = (
    [(f, b) for f in ("text-primary", "text-muted", "text-subtle", "accent",
                      "danger", "success", "warning")
     for b in SURFACES + ("accent-subtle",)]
    + [("text-on-accent", "accent"), ("text-on-accent", "accent-hover"),
       # the three notices, the reader's questions, the sign-in error
       ("warning", "warning-subtle"), ("success", "success-subtle"),
       ("danger", "danger-subtle"), ("accent", "warning-subtle"),
       ("accent-hover", "bg"), ("accent-hover", "surface")])
# Boundaries that identify something, at 3:1: a field's edge, the focus
# ring, and every bar that carries a meaning (budget state, the limit, a
# category's share) against the track and the cards it sits on.
APP_UI = [(f, b) for f in ("border-strong", "accent", "success", "warning",
                           "danger", "untracked")
          for b in SURFACES]

WRITEUP_TEXT = (
    [(f, b) for f in ("text-primary", "text-muted", "text-subtle", "accent")
     for b in SURFACES + ("accent-subtle",)]
    + [("text-on-accent", "accent"), ("text-on-accent", "accent-hover"),
       ("success", "surface"), ("success", "bg"),
       ("danger", "surface"), ("danger", "bg")])
WRITEUP_UI = [("border-strong", "bg"), ("border-strong", "surface"),
              ("accent", "bg"), ("accent", "surface"), ("accent", "track"),
              ("text-subtle", "surface"), ("success", "surface"),
              ("danger", "surface")]


# ------------------------------------------------------------- reading them

def _css(path):
    """The CSS of a stylesheet, or of the <style> blocks in a page, with
    comments removed -- a comment explaining a rule contains the rule."""
    text = _read(path)
    if path.endswith(".html"):
        text = "\n".join(re.findall(r"<style>(.*?)</style>", text, re.S))
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def theme_blocks(css):
    """{theme name: {token: value}} for every `.theme-<name>` rule."""
    blocks = {}
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        for name in re.findall(r"\.theme-([a-z]+)\b", selector):
            blocks[name] = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+?)\s*;",
                                           body))
    return blocks


def themes_of(source):
    found = re.search(r"var THEMES = \[([^\]]*)\];", source)
    assert found, "no THEMES list to step through"
    return re.findall(r"'([a-z]+)'", found.group(1))


def _hex(value):
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    assert re.fullmatch(r"[0-9a-fA-F]{6}", value), value
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _luminance(rgb):
    def channel(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(fg, bg):
    light, dark = sorted((_luminance(_hex(fg)), _luminance(_hex(bg))),
                         reverse=True)
    return (light + 0.05) / (dark + 0.05)


def shortfalls(block, text_pairs, ui_pairs):
    """Every pair in one theme block below its threshold, as readable lines."""
    short = []
    for pairs, need in ((text_pairs, 4.5), (ui_pairs, 3.0)):
        for fg, bg in pairs:
            got = ratio(block["--color-" + fg], block["--color-" + bg])
            if got < need:
                short.append("%s on %s %.2f:1 (needs %s)" % (fg, bg, got, need))
    return short


PALETTES = {
    "app": (APP_SHEETS, themes_of(_read(THEME_JS[0])), APP_TEXT, APP_UI),
    "write-up": ({"write-up": WRITEUP}, themes_of(_read(WRITEUP)),
                 WRITEUP_TEXT, WRITEUP_UI),
}


# ------------------------------------------------------------ the blocks

@pytest.mark.parametrize("name,path", sorted(dict(
    APP_SHEETS, **{"write-up": WRITEUP}).items()))
def test_every_theme_has_a_block_setting_the_same_tokens(name, path):
    themes = PALETTES["write-up" if path == WRITEUP else "app"][1]
    blocks = theme_blocks(_css(path))
    assert themes[0] == "dark", "the page opens dark, so dark comes first"
    default = set(blocks.get(themes[0], ()))
    assert CONTRACT <= default, (
        f"{name}: the default theme is missing {sorted(CONTRACT - default)}")
    for theme in themes:
        assert theme in blocks, f"{name}: THEMES names {theme!r} but there " \
                                f"is no .theme-{theme} block"
        assert set(blocks[theme]) == default, (
            f"{name}: .theme-{theme} differs from the default by "
            f"{sorted(set(blocks[theme]) ^ default)}")


@pytest.mark.parametrize("path", sorted(dict(
    APP_SHEETS, **{"write-up": WRITEUP}).values()))
def test_the_dark_block_is_also_the_fallback(path):
    """`:where(:root)` has no specificity, so an <html> with no theme class,
    or with one from a theme since removed, still gets every token."""
    assert re.search(r":where\(:root\),\s*\.theme-dark\s*\{", _css(path)), (
        f"{os.path.relpath(path, ROOT)} has no :where(:root) fallback")


def test_the_three_app_stylesheets_carry_one_palette():
    """They are served separately -- Flask's static/ and two GitHub Pages
    folders -- so the palette is written three times. It is one palette."""
    blocks = {name: theme_blocks(_css(path))
              for name, path in APP_SHEETS.items()}
    first = blocks["app"]
    for name, found in blocks.items():
        assert found == first, (
            f"the {name} stylesheet's theme blocks differ from the app's")


def test_the_theme_script_is_one_file_in_three_places():
    first = _read(THEME_JS[0])
    for path in THEME_JS[1:]:
        assert _read(path) == first, (
            f"{os.path.relpath(path, ROOT)} has drifted from docs/app/theme.js")


@pytest.mark.parametrize("palette", sorted(PALETTES))
def test_every_pairing_meets_wcag_aa_in_every_theme(palette):
    sheets, themes, text_pairs, ui_pairs = PALETTES[palette]
    for name, path in sheets.items():
        blocks = theme_blocks(_css(path))
        for theme in themes:
            short = shortfalls(blocks[theme], text_pairs, ui_pairs)
            assert not short, f"{name}, {theme} theme: {short}"


def test_that_check_would_notice_a_failing_pair():
    """The negative control, kept rather than run once by hand: the old dark
    hint colour, which was 3.23:1 on a list row."""
    block = dict(theme_blocks(_css(APP_SHEETS["app"]))["dark"])
    assert not shortfalls(block, APP_TEXT, APP_UI)
    block["--color-text-subtle"] = "#6b7387"
    assert shortfalls(block, APP_TEXT, APP_UI) == [
        "text-subtle on bg 3.88:1 (needs 4.5)",
        "text-subtle on surface 3.55:1 (needs 4.5)",
        "text-subtle on surface-subtle 3.23:1 (needs 4.5)",
        "text-subtle on accent-subtle 3.03:1 (needs 4.5)"]


RAW_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(")


@pytest.mark.parametrize("name", sorted(ALL_STYLE))
def test_no_rule_outside_the_theme_blocks_carries_a_raw_colour(name):
    """A raw colour in a component is one the toggle cannot reach: it stays
    dark-theme orange on a light page."""
    css = re.sub(r"[^{}]*\.theme-[a-z]+[^{}]*\{[^{}]*\}", "",
                 _css(ALL_STYLE[name]))
    assert not RAW_COLOUR.findall(css), (
        f"{name}: raw colours outside the theme blocks: "
        f"{RAW_COLOUR.findall(css)}")
    page = _read(ALL_STYLE[name])
    inline = " ".join(re.findall(r'style="([^"]*)"', page))
    assert not RAW_COLOUR.findall(inline), f"{name}: raw colour inline"


@pytest.mark.parametrize("path", PAGES,
                         ids=[os.path.relpath(p, ROOT) for p in PAGES])
def test_every_page_opens_dark_and_follows_no_system_setting(path):
    page = _read(path)
    assert re.search(r'<html lang="en" class="theme-dark">', page), (
        "the page must be dark from its markup, with no script involved")
    assert "prefers-color-scheme" not in page and "data-theme" not in page, (
        "the theme follows the visitor's choice, not the system's")
    head = page.split("</head>", 1)[0]
    restore = head.find("theme.js") if "theme.js" in head else \
        head.find("localStorage.getItem('theme')")
    styles = re.search(r'<link rel="stylesheet" href="[^"]*css|<style>', head)
    assert 0 <= restore < styles.start(), (
        "the saved theme is restored in <head>, before the stylesheet, so a "
        "visitor who chose light never sees the page flash dark")
    assert re.search(r'<button[^>]*id="themeBtn"[^>]*type="button"|'
                     r'<button[^>]*type="button"[^>]*id="themeBtn"', page)


@pytest.mark.parametrize("path", list(APP_SHEETS.values()) + [
    _at("standalone", "pounds", "limit.css")])
def test_no_stylesheet_follows_the_system_setting(path):
    assert "prefers-color-scheme" not in _read(path)


def test_the_theme_key_is_none_of_the_keys_purchases_are_kept_under():
    """'theme' is shared with every page on the origin on purpose. The
    purchases must not be: a collision would wipe one or the other."""
    keys = []
    for folder in (("docs", "app"), ("docs", "capture"),
                   ("standalone", "pounds")):
        found = re.search(r"var KEY = '([^']+)';",
                          _read(_at(*folder, "app.js")))
        keys.append(found.group(1))
    assert len(keys) == 3 and "theme" not in keys
    assert all(key.startswith("wallet.") for key in keys), keys
    for path in THEME_JS + [WRITEUP]:
        assert set(re.findall(r"localStorage\.\w+Item\('([^']+)'",
                              _read(path))) == {"theme"}


# ------------------------------------------------------------ in a browser

BROWSERS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
)


def browser():
    for path in BROWSERS:
        if os.path.isfile(path):
            return path
    return shutil.which("google-chrome") or shutil.which("chromium")


def _url(path):
    return "file:///" + path.replace("\\", "/").lstrip("/")


# The page-side half: the same contrast scan the theming was verified with,
# run over every visible text node.
HARNESS = r"""<!doctype html><meta charset="utf-8"><body><pre id="out"></pre>
<script>
var PAGES = %s;
function scan(doc) {
  var win = doc.defaultView;
  function parse(c) { var n = c.match(/[\d.]+/g).map(Number); var unit = c.indexOf('color(') === 0 ? 1 : 255;
                      return { rgb: n.slice(0, 3).map(function (v) { return v / unit; }), a: n.length > 3 ? n[3] : 1 }; }
  function lum(c) { var x = parse(c).rgb.map(function (v) { return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); });
                    return .2126 * x[0] + .7152 * x[1] + .0722 * x[2]; }
  function behind(el) { for (var e = el; e; e = e.parentElement) { var bg = win.getComputedStyle(e).backgroundColor;
                        if (/\d/.test(bg) && parse(bg).a > .5) return bg; } return win.getComputedStyle(doc.body).backgroundColor; }
  var out = [];
  doc.querySelectorAll('body *').forEach(function (el) {
    if (![].some.call(el.childNodes, function (n) { return n.nodeType === 3 && n.textContent.trim(); })) return;
    var s = win.getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || !el.getClientRects().length) return;
    if (el.closest('[hidden], noscript, datalist, option, select')) return;
    var big = parseFloat(s.fontSize) >= 24 || (parseFloat(s.fontSize) >= 18.66 && +s.fontWeight >= 700);
    var L = [lum(s.color), lum(behind(el))].sort(function (a, b) { return b - a; });
    var r = (L[0] + .05) / (L[1] + .05);
    if (r < (big ? 3 : 4.5)) out.push(el.tagName + '.' + el.className + ' ' + r.toFixed(2) + ' "' + el.textContent.trim().slice(0, 24) + '"');
  });
  return out;
}
/* Every state a fresh page does not show: each notice colour, the reader's
   questions, and their containers. */
function inEveryState(doc) {
  var found = scan(doc);
  var notice = doc.getElementById('notice');
  if (notice) {
    var was = [notice.hidden, notice.className, notice.textContent];
    ['notice', 'notice good', 'notice bad', 'notice warn'].forEach(function (cls) {
      notice.hidden = false; notice.className = cls; notice.textContent = 'A notice: ' + cls;
      found = found.concat(scan(doc));
    });
    notice.hidden = was[0]; notice.className = was[1]; notice.textContent = was[2];
  }
  var needs = doc.getElementById('needs');
  if (needs) {
    var shown = [];
    for (var e = needs; e && e !== doc.body; e = e.parentElement) if (e.hidden) { e.hidden = false; shown.push(e); }
    var text = needs.innerHTML;
    needs.innerHTML = '<p>The 7th of September or the 9th of July? <button class="linky" type="button">9 July</button></p>';
    found = found.concat(scan(doc));
    needs.innerHTML = text;
    shown.forEach(function (e) { e.hidden = true; });
  }
  return found.filter(function (v, i, a) { return a.indexOf(v) === i; });
}
function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function load(frame, go) { return new Promise(function (r) { frame.onload = function () { r(); }; go(); }); }
function themeOf(doc) { return (doc.documentElement.className.match(/(?:^|\s)theme-(\S+)/) || [])[1]; }
async function run() {
  var out = {};
  for (var i = 0; i < PAGES.length; i++) {
    var page = PAGES[i], r = out[page.name] = {};
    /* 1. scripts disabled: a sandbox without allow-scripts */
    var still = document.createElement('iframe');
    still.setAttribute('sandbox', 'allow-same-origin');
    still.style.cssText = 'width:1100px;height:900px';
    document.body.appendChild(still);
    await load(still, function () { still.src = page.url; }); await wait(300);
    var d = still.contentDocument, w = still.contentWindow;
    r.nojs = { cls: d.documentElement.className, bg: w.getComputedStyle(d.body).backgroundColor,
               scheme: w.getComputedStyle(d.documentElement).colorScheme, short: scan(d) };
    still.remove();

    /* 2. scripts on */
    var f = document.createElement('iframe');
    f.style.cssText = 'width:1100px;height:900px';
    document.body.appendChild(f);
    await load(f, function () { f.src = page.url; });
    f.contentWindow.localStorage.removeItem('theme');
    await load(f, function () { f.contentWindow.location.reload(); }); await wait(600);
    d = f.contentDocument; w = f.contentWindow;
    var btn = d.getElementById('themeBtn'), meta = d.querySelector('meta[name="theme-color"]');
    r.themes = w.THEMES;
    r.opened = [themeOf(d), btn.getAttribute('aria-label'), btn.hidden, meta ? meta.content : null];
    r.dark = inEveryState(d);
    btn.click(); await wait(200);
    r.toggled = [themeOf(d), w.localStorage.getItem('theme'), btn.getAttribute('aria-label'),
                 meta ? meta.content : null, w.getComputedStyle(d.body).backgroundColor];
    r.light = inEveryState(d);
    await load(f, function () { f.contentWindow.location.reload(); }); await wait(500);
    r.remembered = themeOf(f.contentDocument);
    f.contentWindow.localStorage.setItem('theme', 'sepia');
    await load(f, function () { f.contentWindow.location.reload(); }); await wait(500);
    d = f.contentDocument;
    r.unknown = [(d.documentElement.className.match(/theme-\S+/g) || []).join(),
                 f.contentWindow.getComputedStyle(d.body).backgroundColor];
    f.contentWindow.localStorage.removeItem('theme');
    f.remove();
  }
  return out;
}
run().then(function (out) { document.getElementById('out').textContent = 'RESULT ' + JSON.stringify(out); },
           function (e) { document.getElementById('out').textContent = 'RESULT ' + JSON.stringify({ error: String(e && e.stack || e) }); });
</script></body>"""


def _render_templates(folder):
    """The three Flask pages as the server renders them, written to disk
    with their static files pointed back at the repository's."""
    import app as web
    application = web.create_app(folder)
    pages = {}
    with application.test_request_context("/"):
        from flask import render_template
        for name, kwargs in (
                ("index.html", {"csrf_token": "t"}),
                ("simple.html", {"csrf_token": "t"}),
                # the error state, which only a refused sign-in shows
                ("login.html", {"csrf_token": "t", "next": "/", "wait": 0,
                                "error": "Too many attempts. Try again in 30 "
                                         "seconds."})):
            text = render_template(name, **kwargs)
            text = text.replace('"/static/', '"' + _url(_at("static")) + "/")
            path = os.path.join(folder, "flask-" + name)
            with io.open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            pages["templates/" + name] = path
    return pages


@pytest.fixture(scope="module")
def rendered():
    folder = tempfile.mkdtemp()
    try:
        pages = {os.path.relpath(p, ROOT).replace("\\", "/"): p
                 for p in PAGES if not p.startswith(_at("templates"))}
        pages.update(_render_templates(folder))
        listing = [{"name": name, "url": _url(path)}
                   for name, path in sorted(pages.items())]
        harness = os.path.join(folder, "harness.html")
        with io.open(harness, "w", encoding="utf-8") as handle:
            handle.write(HARNESS % json.dumps(listing))
        done = subprocess.run(
            [browser(), "--headless=new", "--disable-gpu", "--no-sandbox",
             "--no-first-run", "--no-default-browser-check",
             "--allow-file-access-from-files",
             "--user-data-dir=" + os.path.join(folder, "profile"),
             "--virtual-time-budget=90000", "--dump-dom", _url(harness)],
            capture_output=True, timeout=300)
        dom = done.stdout.decode("utf-8", "replace")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    found = re.search(r"RESULT (\{.*?\})</pre>", dom, re.S)
    assert found, f"the harness did not finish; {len(dom)} bytes of DOM"
    result = json.loads(html.unescape(found.group(1)))
    assert "error" not in result, result.get("error")
    assert len(result) == len(PAGES), sorted(result)
    return result


def _rgb(value):
    return "rgb(%d, %d, %d)" % _hex(value)


def _palette_for(name):
    if name == "docs/index.html":
        return theme_blocks(_css(WRITEUP))
    if name.startswith("templates/"):
        return theme_blocks(_css(APP_SHEETS["app"]))
    if name.startswith("docs/capture/"):
        return theme_blocks(_css(APP_SHEETS["capture"]))
    return theme_blocks(_css(APP_SHEETS["static wallet"]))


needs_browser = pytest.mark.skipif(not browser(),
                                   reason="no browser to render the pages in")


@needs_browser
def test_with_scripts_disabled_every_page_is_dark(rendered):
    for name, got in rendered.items():
        dark = _palette_for(name)["dark"]
        assert got["nojs"]["cls"] == "theme-dark", name
        assert got["nojs"]["bg"] == _rgb(dark["--color-bg"]), (
            f"{name}: with no JavaScript the body is {got['nojs']['bg']}")
        assert got["nojs"]["scheme"] == "dark", name
        assert not got["nojs"]["short"], (name, got["nojs"]["short"][:4])


@needs_browser
def test_the_toggle_switches_and_remembers(rendered):
    for name, got in rendered.items():
        light = _palette_for(name)["light"]
        meta = got["opened"][3]
        assert got["themes"][:2] == ["dark", "light"], name
        assert got["opened"][:3] == ["dark", "Switch to light theme", False], (
            name, got["opened"])
        assert got["toggled"][:3] == ["light", "light",
                                      "Switch to dark theme"], (
            name, got["toggled"])
        assert got["toggled"][4] == _rgb(light["--color-bg"]), name
        if meta is not None:
            assert meta == _palette_for(name)["dark"]["--color-bg"], name
            assert got["toggled"][3] == light["--color-bg"], (
                f"{name}: the browser toolbar did not follow the page")
        assert got["remembered"] == "light", f"{name} forgot the choice"


@needs_browser
def test_an_unknown_saved_theme_falls_back_to_dark(rendered):
    for name, got in rendered.items():
        dark = _palette_for(name)["dark"]
        assert got["unknown"] == ["theme-dark", _rgb(dark["--color-bg"])], (
            name, got["unknown"])


@needs_browser
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_every_visible_text_meets_aa(rendered, theme):
    for name, got in rendered.items():
        assert not got[theme], (name, got[theme][:6])
