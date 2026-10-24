/* "You were last here on …" — the marker a returning reader sees.
 *
 * Each browser remembers the newest daily edition it has opened
 * (localStorage, nothing leaves the browser). Opening a newer one shows a
 * line under the edition head linking back to the last one read, then
 * records the new edition. Opening an older issue never moves the marker
 * backwards. A file, not inline: the vhost's CSP drops inline scripts.
 */
(function () {
  "use strict";
  var KEY = "tl.seen";
  var body = document.body;
  var edition = body.getAttribute("data-edition");
  if (!edition) return;
  var last = null;
  try { last = window.localStorage.getItem(KEY); } catch (e) { return; }
  if (last && last < edition) {
    var box = document.getElementById("since");
    var tpl = body.getAttribute("data-since") || "{d}";
    if (box && tpl.indexOf("{d}") >= 0) {
      var cut = tpl.split("{d}");
      var a = document.createElement("a");
      a.href = body.getAttribute("data-daily") + last + ".html";
      a.textContent = last;
      box.textContent = "";
      box.appendChild(document.createTextNode(cut[0]));
      box.appendChild(a);
      box.appendChild(document.createTextNode(cut[1]));
      box.hidden = false;
    }
  }
  if (!last || last < edition) {
    try { window.localStorage.setItem(KEY, edition); } catch (e) { /* private mode */ }
  }
})();
