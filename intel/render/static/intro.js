/* The opening: one stage where the mark, the name and the line fade up one
 * after another and stay. 1.5 s after the line is up the splash drifts up and
 * fades away over the page; Skip ends it sooner.
 *
 * It plays on a first visit. The splash carries a "don't show this again"
 * box, ticked by default: closing it with the box ticked stores tl_intro=off
 * and the page opens straight away from then on; unticked stores
 * tl_intro=always and it plays on every visit. The footer's replay link plays
 * it on demand. A reader who asks the system for reduced motion is not shown
 * it unasked.
 *
 * Loaded in <head> without defer so the class that shows the splash is set
 * before the body paints, and the page never flashes first. A file, not
 * inline: the vhost's CSP drops inline scripts.
 */
(function () {
  "use strict";
  var KEY = "tl_intro";
  var LINE_UP_MS = 3600;  // the line has faded up, per style.css
  var HOLD_MS = 1500;     // how long the whole stage holds before leaving
  var FADE_MS = 1000;     // the splash's own exit, per style.css
  var root = document.documentElement;

  function stored() {
    var m = document.cookie.match(/(?:^|;\s*)tl_intro=([a-z]+)/);
    return m ? m[1] : null;
  }

  function store(value) {
    document.cookie = KEY + "=" + value + "; Max-Age=31536000; Path=/; SameSite=Lax" +
      (location.protocol === "https:" ? "; Secure" : "");
  }

  var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var choice = stored();
  if (choice !== "off" && !(calm && choice !== "always")) {
    root.classList.add("intro-play");
  }

  document.addEventListener("DOMContentLoaded", function () {
    var box = document.getElementById("intro");
    var once = document.getElementById("intro-once");
    var skip = document.getElementById("intro-skip");
    var replay = document.getElementById("intro-replay");
    if (!box) return;
    var timer = null;

    function close() {
      if (!root.classList.contains("intro-play") || box.classList.contains("out")) return;
      clearTimeout(timer);
      store(once && !once.checked ? "always" : "off");
      box.classList.add("out");
      setTimeout(function () {
        root.classList.remove("intro-play");
        box.classList.remove("run", "out");
      }, FADE_MS);
    }

    function run() {
      if (once) once.checked = stored() !== "always";
      box.classList.remove("run", "out");
      root.classList.add("intro-play");
      void box.offsetWidth;   // restart the CSS animations on a replay
      box.classList.add("run");
      if (skip) skip.focus({ preventScroll: true });
      clearTimeout(timer);
      timer = setTimeout(close, (calm ? 600 : LINE_UP_MS) + HOLD_MS);
    }

    if (skip) skip.addEventListener("click", close);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") close();
    });
    if (once) once.addEventListener("change", function () {
      store(once.checked ? "off" : "always");
    });
    if (replay) {
      replay.hidden = false;
      replay.addEventListener("click", function (e) { e.preventDefault(); run(); });
    }
    if (root.classList.contains("intro-play")) run();
  });
})();
