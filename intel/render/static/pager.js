/* ← / → walk the pager.
 *
 * The links themselves are <link rel="prev"|"next"> in <head>, written by
 * whichever template has neighbours (issue pages, archive calendars). This
 * script reads those and nothing else, so a page grows keyboard paging by
 * declaring its neighbours rather than by being listed here.
 *
 * It is a file and not an inline <script> on purpose: the vhost sends
 * `default-src 'self'` with no `script-src 'unsafe-inline'`, so an inline
 * handler is dropped by the browser without an error the page can see.
 */
(function () {
  "use strict";

  function href(rel) {
    var el = document.querySelector('link[rel="' + rel + '"]');
    return el ? el.getAttribute("href") : null;
  }

  function typing() {
    var el = document.activeElement;
    if (!el) return false;
    var tag = el.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
           el.isContentEditable === true;
  }

  document.addEventListener("keydown", function (ev) {
    // Modifier combinations belong to the browser: ⌘←/Alt+← is Back, and
    // stealing it would make the pager impossible to escape.
    if (ev.defaultPrevented || ev.altKey || ev.ctrlKey ||
        ev.metaKey || ev.shiftKey) return;
    if (typing()) return;
    var target = ev.key === "ArrowLeft" ? href("prev")
               : ev.key === "ArrowRight" ? href("next")
               : null;
    if (!target) return;
    ev.preventDefault();
    window.location.href = target;
  });
})();
