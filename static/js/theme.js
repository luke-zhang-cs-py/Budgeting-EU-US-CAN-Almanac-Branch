/* theme.js -- the page's colour theme. Loaded in <head>, before the stylesheet.
 *
 * Not needed to theme the page: class="theme-dark" on <html> already does
 * that, with or without JavaScript. This does two small things on top of it:
 * it restores a theme the visitor picked before, ahead of first paint so the
 * page never flashes dark first, and it wires the toggle (#themeBtn).
 *
 * The key is 'theme', and deliberately not 'wallet.something' like the
 * purchase stores: every project page on this GitHub Pages origin reads the
 * same key, so one choice carries across the whole site. The purchase keys
 * are all namespaced 'wallet.*.v1', so the two can never collide.
 *
 * THEMES is the order the toggle steps through. Each name needs a matching
 * .theme-<name> block in the stylesheet, and the first is the one written
 * into the markup. Adding a theme is one block there and one entry here.
 *
 * The same file is in docs/app/, docs/capture/ and static/js/, because each
 * of those is served on its own (the pound wallet borrows docs/app's, as it
 * does fx.js). tests/test_theme.py holds the copies identical. */
var THEMES = ['dark', 'light'];

(function () {
  var root = document.documentElement;
  var saved = null;
  try { saved = window.localStorage.getItem('theme'); } catch (e) { /* storage blocked */ }
  if (/^[a-z]+$/.test(saved)) root.classList.replace('theme-dark', 'theme-' + saved);

  var theme = THEMES[0];
  function nextTheme() { return THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length]; }

  function applyTheme(name, button) {
    root.className = root.className.replace(/(^|\s)theme-\S+/g, '').trim() + ' theme-' + name;
    theme = name;
    button.setAttribute('aria-label', 'Switch to ' + nextTheme() + ' theme');
    button.title = 'Switch to ' + nextTheme() + ' theme';
    /* The browser's own toolbar follows the page. */
    var metas = document.querySelectorAll('meta[name="theme-color"]');
    for (var i = 0; i < metas.length; i++) {
      metas[i].setAttribute('content', getComputedStyle(root).getPropertyValue('--color-bg').trim());
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    var button = document.getElementById('themeBtn');
    if (!button) return;
    button.hidden = false;
    /* A saved name with no block of its own (a theme since removed, or one
       from another page on this origin) falls back to the first. */
    theme = THEMES.filter(function (t) { return root.classList.contains('theme-' + t); })[0] || THEMES[0];
    applyTheme(theme, button);
    button.addEventListener('click', function () {
      var name = nextTheme();
      try { window.localStorage.setItem('theme', name); } catch (e) { /* not remembered */ }
      applyTheme(name, button);
    });
  });
})();
