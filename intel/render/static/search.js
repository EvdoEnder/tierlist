/* tierlist /search/ — client-side index filter.

   No frameworks: the index is a static JSON blob (search-index.json,
   shipped beside the page by the nightly sweep). The page bakes the i18n
   strings and the index URL onto <body data-*> (see _base.html.j2), so
   this file reads them as attributes — no inline <script>, which the CSP
   ('self' only, no 'unsafe-inline') would silently drop. Matching priority:
     1. project name (owner is inside the name, first token wins),
     2. tags,
     3. owner (the part before the slash of the github id),
     4. description.
   A query that starts with '#' is a TAG query instead: it matches only
   entries carrying that exact tag, so `#ai` finds the tag and not every
   description with "ai" inside "chain". The tag pages are the same
   population as this index, so a tag on a page is always findable here.
   Results float by match-hits, then tier, then date-desc; the top 50
   print. Debounced at 150 ms so fast typing never lags. */
(function () {
  var Q = '';
  var INDEX = [];
  var B = document.body;
  var I18N = {
    emptyQuery: B.getAttribute('data-empty-query') || '',
    noResults: B.getAttribute('data-no-results') || '',
    results: B.getAttribute('data-results') || '{n} results',
    indexCount: B.getAttribute('data-index-count') || '{n} indexed',
    unrated: B.getAttribute('data-unrated') || 'unrated'
  };
  var IDX_SRC = B.getAttribute('data-index') || 'search-index.json';
  /* <html lang> is "zh-CN" / "en"; the index keys its labels by the bare
     language, and the /zh/ tree serves its own copy of this page. */
  var LANG = (document.documentElement.lang || 'zh').slice(0, 2);

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function qs(q) {
    if (!q) return INDEX.slice(0, 50);
    var w = q.toLowerCase().trim();
    if (w.charAt(0) === '#') return byTag(w.slice(1));
    var out = [];
    for (var i = 0; i < INDEX.length; i++) {
      var r = INDEX[i];
      var name = (r.name || '').toLowerCase();
      var tags = tagForms(r).join(' ').toLowerCase();
      var owner = (r.id || '').split(':').join(' ').split('/')[0].toLowerCase();
      var desc = (r.description || '').toLowerCase();
      var hits = 0;
      if (name.indexOf(w) >= 0) hits += 4;
      if (tags.indexOf(w) >= 0) hits += 3;
      if (owner.indexOf(w) >= 0) hits += 2;
      if (desc.indexOf(w) >= 0) hits += 1;
      if (hits) {
        r._h = hits;
        out.push(r);
      }
    }
    out.sort(function (a, b) {
      if (b._h !== a._h) return b._h - a._h;
      if (score(b) !== score(a)) return score(b) - score(a);
      return (a.when || '') < (b.when || '') ? 1 : -1;
    });
    return out.slice(0, 50);
  }

  var TIER_RANK = {S: 5, A: 4, B: 3, C: 2, D: 1};
  function score(r) {
    return TIER_RANK[r.tier] || 0;
  }

  /* Every spelling of an entry's tags: the stored ASCII slug plus both
     display names. A tag is stored as `crack` and printed as 破解 on the
     Chinese site and Crack on the English one, so all three have to match
     or a reader who types what is on screen gets nothing. */
  function tagForms(r) {
    var out = (r.tags || []).slice();
    var L = r.tag_labels || {};
    ['zh', 'en'].forEach(function (k) {
      (L[k] || []).forEach(function (v) { out.push(v); });
    });
    return out;
  }

  /* Exact tag match, case-insensitive. An empty '#' lists nothing rather
     than everything: the user is mid-word, and dumping the whole index
     under a lone '#' reads as a bug. */
  function byTag(tag) {
    if (!tag) return [];
    var out = [];
    for (var i = 0; i < INDEX.length; i++) {
      var r = INDEX[i];
      var tags = tagForms(r);
      for (var j = 0; j < tags.length; j++) {
        if (String(tags[j]).toLowerCase() === tag) {
          r._h = 3;
          out.push(r);
          break;
        }
      }
    }
    out.sort(function (a, b) {
      if (score(b) !== score(a)) return score(b) - score(a);
      return (a.when || '') < (b.when || '') ? 1 : -1;
    });
    return out.slice(0, 50);
  }

  function row(r) {
    var d = el('div', 'trow');
    d.appendChild(el('span', 'mono dt', r.when || ''));
    var n = el('span', 'n');
    var a = el('a', null, r.name);
    a.href = r.url;
    a.target = '_blank';
    a.rel = 'noopener';
    n.appendChild(a);
    d.appendChild(n);
    /* tier is null for an item that was tagged but never rated — it is
       in the index because the tag pages link to it. Like the tag pages,
       it gets no ring: the cell stays empty and the word is for screen
       readers only. */
    if (r.tier) {
      d.appendChild(el('span', 'sc' + (r.tier === 'S' || r.tier === 'A' ? ' hi' : ''),
                       r.tier));
    } else {
      d.appendChild(el('span', 'vh', I18N.unrated));
    }
    if (r.description) {
      d.appendChild(el('span', 'd', r.description));
    }
    /* Print what the tag pages print: the label for THIS page's language,
       falling back to the slug for an entity (`ffmpeg` has no translation
       and should not acquire one). */
    var shown = (r.tag_labels && r.tag_labels[LANG]) || r.tags;
    if (shown && shown.length) {
      var tags = el('span', 'stags');
      for (var i = 0; i < Math.min(shown.length, 6); i++) {
        tags.appendChild(el('span', 'stag', '#' + shown[i]));
      }
      d.appendChild(tags);
    }
    return d;
  }

  function render(q) {
    var list = document.getElementById('sr-list');
    var count = document.getElementById('sr-count');
    if (!list) return;
    list.innerHTML = '';
    var res = qs(q);
    if (count) {
      count.textContent = q
        ? (I18N.results || '{n} results').replace('{n}', String(res.length))
        : (I18N.indexCount || '{n} indexed').replace('{n}', String(INDEX.length));
    }
    if (!q) {
      list.appendChild(el('p', 'pending', I18N.emptyQuery || ''));
      return;
    }
    if (!res.length) {
      list.appendChild(el('p', 'pending', I18N.noResults || ''));
      return;
    }
    res.forEach(function (r) { list.appendChild(row(r)); });
  }

  function load() {
    fetch(IDX_SRC, { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (j) { INDEX = (j && j.items) || []; render(Q); })
      .catch(function () { render(Q); });
  }

  (function init() {
    var box = document.getElementById('sr-q');
    if (!box) return;
    // The masthead's search box on every other page submits here as ?q=,
    // so a query typed anywhere on the site lands already run.
    var pre = new URLSearchParams(window.location.search).get('q');
    if (pre) { box.value = pre; Q = pre; }
    var timer = null;
    box.addEventListener('input', function () {
      Q = box.value;
      clearTimeout(timer);
      timer = setTimeout(function () { render(Q); }, 150);
    });
    box.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') { clearTimeout(timer); render(Q); }
    });
    load();
  })();
})();
