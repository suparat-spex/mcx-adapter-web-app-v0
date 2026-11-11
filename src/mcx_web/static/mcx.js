// MCX Adapter web console - progressive enhancements. Every page also works as plain forms and links without it.
(function () {
  var root = document.documentElement;
  root.classList.add("js");

  // theme: system (default) / dark / light, saved per browser
  var themeMode = "system";
  try { themeMode = localStorage.getItem("mcx-theme") || "system"; } catch (e) {}
  function applyTheme() {
    var dark = themeMode === "dark" || (themeMode !== "light" && window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches);
    root.setAttribute("data-theme", dark ? "dark" : "light");
  }
  applyTheme();
  if (window.matchMedia) matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () { if (themeMode === "system") applyTheme(); });

  function store(k, v) { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) {} }
  function stored(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function mq(q) { return !!(window.matchMedia && matchMedia(q).matches); }

  // right panel: width and open / closed are applied here, in <head>, BEFORE the page is drawn - so a page
  // never shows the default width or an open panel first and then slides to the saved state
  var OVERLAY = "(min-width:901px) and (max-width:1100px)", STACK = "(max-width:900px)";
  // left: the main sidebar shows labels ("full") or icons only ("icons"); the section menu always stays.
  // Default: labels from 1600 px, icons below (notebooks). The user's choice wins.
  var navMode = stored("mcx-nav-mode") || (window.innerWidth >= 1600 ? "full" : "icons");
  if (!mq(STACK) && navMode !== "full") root.classList.add("nav-icons");
  function navWidth() {
    if (mq(STACK)) return 0;
    return (root.classList.contains("nav-icons") ? 52 : 200) + (window.innerWidth <= 1280 ? 220 : 240);
  }
  function sideDefault() { return window.innerWidth <= 1366 ? 300 : 340; }
  function sideClamp(w) {
    var max = Math.min(640, window.innerWidth * 0.5, window.innerWidth - navWidth() - 520);
    return Math.round(Math.max(280, Math.min(Math.max(280, max), w)));
  }
  function sideChosen() { return parseInt(stored("mcx-side-w"), 10) || sideDefault(); }   // what the user picked
  root.style.setProperty("--side-w", sideClamp(sideChosen()) + "px");
  var hasSel = /[?&]sel=/.test(location.search);
  // right panel: hidden by default on notebooks (<= 1366 px) until a row is picked; "1" / "0" = the user's choice
  var sidePref = stored("mcx-side-closed");
  var sideShut = mq(OVERLAY) ? !hasSel : !hasSel && (sidePref === "1" || (sidePref === null && window.innerWidth <= 1366));
  if (!mq(STACK) && sideShut) root.classList.add("side-closed");
  root.classList.add("no-anim");                                 // no slide while the page loads
  window.addEventListener("load", function () {
    requestAnimationFrame(function () { requestAnimationFrame(function () { root.classList.remove("no-anim"); }); });
  });

  function csrf() { var m = document.querySelector('meta[name="csrf"]'); return m ? m.content : ""; }
  function post(url, data) {
    var body = new URLSearchParams(data || {}); body.set("csrf", csrf());
    return fetch(url, { method: "POST", body: body, headers: { "X-CSRF": csrf() }, credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status + (r.status >= 500 ? " - server error, see the console log" : ""));
        return r.json();
      });
  }
  function typing(e) {
    var t = e.target;
    return t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
  }
  var SVG = '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
  var ICON = {
    warn: SVG + '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>',
    ask: SVG + '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/></svg>'
  };

  document.addEventListener("DOMContentLoaded", function () {
    var body = document.querySelector(".app>.body");

    // ------------------------------------------------------------ theme button
    function markTheme() {
      document.querySelectorAll("[data-theme-set]").forEach(function (b) {
        var on = b.dataset.themeSet === themeMode;
        b.classList.toggle("on", on); b.setAttribute("aria-checked", on ? "true" : "false");
      });
    }
    markTheme();
    document.addEventListener("click", function (e) {
      var b = e.target.closest("[data-theme-set]");
      if (!b) return;
      themeMode = b.dataset.themeSet; store("mcx-theme", themeMode === "system" ? null : themeMode);
      applyTheme(); markTheme();
    });

    // ------------------------------------------------------------ messages
    function closeMsg(el) { if (!el) return; el.classList.add("hide"); setTimeout(function () { el.remove(); }, 220); }
    document.addEventListener("click", function (e) {
      var b = e.target.closest("[data-close]");
      if (b) closeMsg(b.closest(".toast,.banner"));
    });
    var toast = document.querySelector("[data-toast]");
    if (toast) {
      var held = false;
      ["mouseenter", "focusin"].forEach(function (e) { toast.addEventListener(e, function () { held = true; }); });
      ["mouseleave", "focusout"].forEach(function (e) { toast.addEventListener(e, function () { held = false; }); });
      (function wait(t) { setTimeout(function () { held ? wait(1000) : closeMsg(toast); }, t); })(5000);
    }
    if (/[?&](ok|err)=/.test(location.search) && history.replaceState) {
      var q0 = new URLSearchParams(location.search); q0.delete("ok"); q0.delete("err");
      history.replaceState(null, "", location.pathname + (q0.toString() ? "?" + q0 : "") + location.hash);
    }

    // ------------------------------------------------------------ themed confirm dialog
    var modal = null;
    function ask(opts, onOk) {
      if (!window.HTMLDialogElement) { if (confirm(opts.message)) onOk(); return; }
      if (!modal) {
        modal = document.createElement("dialog");
        modal.className = "modal";
        modal.setAttribute("aria-labelledby", "modal-t");
        modal.setAttribute("aria-describedby", "modal-m");
        modal.innerHTML = '<div class="mi"></div><h2 id="modal-t"></h2><p id="modal-m"></p><div class="acts">' +
          '<button type="button" class="btn" data-v="cancel">Cancel</button>' +
          '<button type="button" class="btn primary" data-v="ok"></button></div>';
        document.body.appendChild(modal);
        modal.addEventListener("click", function (e) {       // click outside the box = cancel
          var r = modal.getBoundingClientRect();
          if (e.target === modal && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)) modal.close();
        });
      }
      modal.classList.toggle("danger", !!opts.danger);
      modal.querySelector(".mi").innerHTML = opts.danger ? ICON.warn : ICON.ask;
      modal.querySelector("#modal-t").textContent = opts.title;
      modal.querySelector("#modal-m").textContent = opts.message;
      var ok = modal.querySelector('[data-v="ok"]'), cancel = modal.querySelector('[data-v="cancel"]');
      ok.textContent = opts.ok;
      ok.onclick = function () { modal.close(); onOk(); };
      cancel.onclick = function () { modal.close(); };
      modal.showModal();
      (opts.danger ? cancel : ok).focus();                    // destructive: Enter does not delete
    }
    document.addEventListener("click", function (e) {         // [data-confirm] buttons, also inside loaded panels
      var b = e.target.closest("[data-confirm]");
      if (!b) return;
      if (b.dataset.confirmed) { delete b.dataset.confirmed; return; }
      e.preventDefault();
      var name = (b.textContent || "").trim() || "OK";
      ask({ title: name + "?", message: b.dataset.confirm, ok: name, danger: b.classList.contains("danger") },
        function () { b.dataset.confirmed = "1"; b.click(); });
    }, true);

    // ------------------------------------------------------------ small behaviours
    document.addEventListener("change", function (e) {        // checkboxes / selects that save at once
      var el = e.target.closest("[data-autosubmit]");
      if (!el) return;
      if (el.hasAttribute("data-psize")) store("mcx-psize", el.value);
      el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit();
    });
    document.querySelectorAll("[data-copy]").forEach(function (b) {
      b.addEventListener("click", function () {
        var el = document.getElementById(b.dataset.copy);
        var text = el ? (el.value || el.textContent) : b.dataset.copy;
        navigator.clipboard.writeText(text).then(function () { var t = b.textContent; b.textContent = "Copied"; setTimeout(function () { b.textContent = t; }, 1500); });
      });
    });
    document.querySelectorAll(".tbox.scroll").forEach(function (box) {   // shadow under the sticky header
      box.addEventListener("scroll", function () { box.classList.toggle("scrolled", box.scrollTop > 2); }, { passive: true });
    });
    // page size: remembered per browser
    var ps = document.querySelector("select[data-psize]"), want = stored("mcx-psize");
    if (ps && want && !new URLSearchParams(location.search).has("size") && ps.value !== want &&
        ps.querySelector('option[value="' + want + '"]')) {
      var qs = new URLSearchParams(location.search); qs.set("size", want); qs.delete("page");
      location.replace(location.pathname + "?" + qs);
      return;
    }

    // ------------------------------------------------------------ user menu (details/summary)
    var menu = null;                                           // the open menu, if any
    function openMenus() { return Array.prototype.slice.call(document.querySelectorAll("details[data-menu][open]")); }
    document.addEventListener("click", function (e) {
      openMenus().forEach(function (m) { if (!m.contains(e.target)) m.open = false; });
    });
    document.addEventListener("toggle", function (e) {
      var m = e.target;
      if (!m.matches || !m.matches("details[data-menu]")) return;
      if (!m.open) { if (menu === m) menu = null; return; }
      openMenus().forEach(function (o) { if (o !== m) o.open = false; });
      menu = m;
      if (m.classList.contains("rowmenu")) {                 // inside a scrolling table: float it above everything
        var r = m.querySelector("summary").getBoundingClientRect(), pop = m.querySelector(".menu-pop");
        pop.style.position = "fixed"; pop.style.right = (window.innerWidth - r.right) + "px";
        var below = window.innerHeight - r.bottom > 260;
        pop.style.top = below ? (r.bottom + 4) + "px" : "auto";
        pop.style.bottom = below ? "auto" : (window.innerHeight - r.top + 4) + "px";
      }
    }, true);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && menu && menu.open) { e.stopPropagation(); menu.open = false; menu.querySelector("summary").focus(); menu = null; }
    }, true);
    window.addEventListener("scroll", function () { openMenus().forEach(function (m) { if (m.classList.contains("rowmenu")) m.open = false; }); }, true);

    // ------------------------------------------------------------ search (centre of the top bar)
    var sf = document.querySelector("[data-search]"), si = document.getElementById("q"), sr = document.getElementById("search-res");
    if (sf && si && sr) {
      var timer = null, seq = 0;
      function links() { return Array.prototype.slice.call(sr.querySelectorAll("a")); }
      function show(open) { sr.hidden = !open; si.setAttribute("aria-expanded", open ? "true" : "false"); }
      function mark(el, text, q) {
        var i = text.toLowerCase().indexOf(q.toLowerCase());
        if (i < 0 || !q) { el.textContent = text; return; }
        el.appendChild(document.createTextNode(text.slice(0, i)));
        var m = document.createElement("mark"); m.textContent = text.slice(i, i + q.length); el.appendChild(m);
        el.appendChild(document.createTextNode(text.slice(i + q.length)));
      }
      function render(groups, q) {
        sr.textContent = "";
        if (!groups.length) {
          var n = document.createElement("div"); n.className = "none"; n.textContent = "No matches for “" + q + "” - Enter searches tools";
          sr.appendChild(n);
        }
        var k = 0;
        groups.forEach(function (g) {
          var h = document.createElement("div"); h.className = "g"; h.textContent = g.title; sr.appendChild(h);
          g.items.forEach(function (it) {
            var a = document.createElement("a"); a.href = it.href; a.id = "sr-" + (k++); a.setAttribute("role", "option");
            var l = document.createElement("span"); l.className = "l"; mark(l, it.label, q);
            var d = document.createElement("span"); d.className = "d"; d.textContent = it.detail;
            a.appendChild(l); a.appendChild(d); sr.appendChild(a);
          });
        });
        var hint = document.createElement("div"); hint.className = "hint"; hint.textContent = "↑ ↓ to move · Enter to open · Esc to close";
        sr.appendChild(hint);
        show(true);
      }
      function move(d) {
        var ls = links(); if (!ls.length) return;
        var i = ls.findIndex(function (a) { return a.classList.contains("on"); });
        if (i >= 0) ls[i].classList.remove("on");
        i = (i + d + ls.length) % ls.length;
        ls[i].classList.add("on"); ls[i].scrollIntoView({ block: "nearest" });
        si.setAttribute("aria-activedescendant", ls[i].id);
      }
      si.addEventListener("input", function () {
        clearTimeout(timer);
        var q = si.value.trim();
        if (!q) { show(false); return; }
        timer = setTimeout(function () {
          var my = ++seq;
          fetch("/api/search?q=" + encodeURIComponent(q), { credentials: "same-origin" })
            .then(function (r) { return r.ok ? r.json() : { groups: [] }; })
            .then(function (j) { if (my === seq && si.value.trim() === q) render(j.groups || [], q); })
            .catch(function () {});
        }, 140);
      });
      si.addEventListener("keydown", function (e) {
        if (e.key === "ArrowDown") { e.preventDefault(); if (sr.hidden && si.value.trim()) show(true); move(1); }
        else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
        else if (e.key === "Enter") { var on = sr.hidden ? null : sr.querySelector("a.on"); if (on) { e.preventDefault(); location.href = on.href; } }
        else if (e.key === "Escape") { e.stopPropagation(); if (!sr.hidden) show(false); else { si.value = ""; si.blur(); } }
      });
      si.addEventListener("focus", function () { if (si.value.trim() && sr.childNodes.length) show(true); });
      document.addEventListener("click", function (e) { if (!sf.contains(e.target)) show(false); });
      document.addEventListener("keydown", function (e) {
        if ((e.key === "k" || e.key === "K") && (e.ctrlKey || e.metaKey)) { e.preventDefault(); si.focus(); si.select(); }
        else if (e.key === "/" && !typing(e) && !e.ctrlKey && !e.metaKey) { e.preventDefault(); si.focus(); }
      });
    }

    // ------------------------------------------------------------ right panel: resize, close, overlay
    var side = document.getElementById("side") || document.querySelector(".app>.body>aside.side");
    var sideBtn = document.getElementById("side-btn");
    var overlayMq = { get matches() { return mq(OVERLAY); } };
    function isClosed() { return root.classList.contains("side-closed"); }
    var splitter = null, chosen = sideChosen();                 // the width the user picked (not measured)
    function setW(w, save) {
      w = sideClamp(w);
      root.style.setProperty("--side-w", w + "px");
      if (splitter) { splitter.setAttribute("aria-valuenow", w); splitter.setAttribute("aria-valuetext", w + " pixels"); }
      if (save) { chosen = w; store("mcx-side-w", String(w)); }
    }
    function setClosed(c, save) {
      root.classList.toggle("side-closed", c);
      if (sideBtn) {
        sideBtn.setAttribute("aria-expanded", c ? "false" : "true");
        sideBtn.classList.toggle("on", !c);
        sideBtn.title = (c ? "Show" : "Hide") + " details panel ( ] )";
      }
      if (save && !overlayMq.matches) store("mcx-side-closed", c ? "1" : "0");
    }
    if (side && body) {
      // no strip and no button on the panel itself: the top-bar button and ] are the only toggles
      // splitter
      splitter = document.createElement("div");
      splitter.className = "splitter"; splitter.tabIndex = 0;
      splitter.setAttribute("role", "separator"); splitter.setAttribute("aria-orientation", "vertical");
      splitter.setAttribute("aria-label", "Resize the right panel (arrow keys, Enter hides)"); splitter.setAttribute("aria-controls", "side");
      splitter.setAttribute("aria-valuemin", "280"); splitter.setAttribute("aria-valuemax", "640");
      side.parentNode.insertBefore(splitter, side);
      setW(chosen, false);
      var dragging = false, dragW = chosen;
      splitter.addEventListener("pointerdown", function (e) {
        if (e.button !== 0) return;
        dragging = true; dragW = chosen; splitter.setPointerCapture(e.pointerId); splitter.classList.add("drag"); document.body.classList.add("resizing");
      });
      splitter.addEventListener("pointermove", function (e) {
        if (!dragging) return;
        var w = body.getBoundingClientRect().right - e.clientX;   // from the pointer, never from the panel's size
        if (w < 200) { setClosed(true, false); } else { setClosed(false, false); dragW = sideClamp(w); setW(dragW, false); }
      });
      function endDrag() {
        if (!dragging) return;
        dragging = false; splitter.classList.remove("drag"); document.body.classList.remove("resizing");
        if (isClosed()) { setClosed(true, true); setW(chosen, false); } else { setClosed(false, true); setW(dragW, true); }
      }
      splitter.addEventListener("pointerup", endDrag);
      splitter.addEventListener("pointercancel", endDrag);
      splitter.addEventListener("dblclick", function () { setW(sideDefault(), true); });
      splitter.addEventListener("keydown", function (e) {
        if (e.key === "ArrowLeft") { e.preventDefault(); setW(chosen + 16, true); }
        else if (e.key === "ArrowRight") { e.preventDefault(); setW(chosen - 16, true); }
        else if (e.key === "Enter") { e.preventDefault(); setClosed(true, true); }
      });
      if (sideBtn) sideBtn.addEventListener("click", function () { setClosed(!isClosed(), true); });
      document.addEventListener("keydown", function (e) {
        if (e.key === "]" && !typing(e) && !e.ctrlKey && !e.metaKey) { e.preventDefault(); setClosed(!isClosed(), true); }
      });
      setClosed(isClosed(), false);                             // state was set in <head>; sync the button
      window.addEventListener("resize", function () { setW(chosen, false); });   // re-fit the chosen width
    }

    // ------------------------------------------------------------ row details in the right panel
    var host = side && side.hasAttribute("data-detail-host") ? side : null;
    var dBody = host && host.querySelector("[data-detail-body]"), dDef = host && host.querySelector("[data-detail-default]");
    function detailRows() {
      return Array.prototype.slice.call(document.querySelectorAll("main tr[data-detail]")).filter(function (r) { return !r.hidden; });
    }
    function setSelParam(v) {
      if (!history.replaceState) return;
      var q = new URLSearchParams(location.search);
      if (v === null) q.delete("sel"); else q.set("sel", v);
      q.delete("seq");
      history.replaceState(null, "", location.pathname + (q.toString() ? "?" + q : "") + location.hash);
    }
    var loadSeq = 0;
    function openDetail(row, focus) {
      if (!host || !row) return;
      detailRows().forEach(function (r) { r.classList.toggle("sel", r === row); });
      if (isClosed()) setClosed(false, !overlayMq.matches);
      host.classList.add("loading");
      var my = ++loadSeq;
      fetch(row.dataset.detail, { credentials: "same-origin" })
        .then(function (r) { if (!r.ok && r.status !== 404) throw new Error("HTTP " + r.status); return r.text(); })
        .then(function (html) {
          if (my !== loadSeq) return;
          dBody.innerHTML = html;                         // server-rendered, escaped by Jinja, same origin
          if (dDef) dDef.hidden = true;
          host.scrollTop = 0;
          setSelParam(row.dataset.sel);
        })
        .catch(function (e) { if (my === loadSeq) { dBody.innerHTML = ""; var p = document.createElement("p"); p.className = "pb bad small"; p.textContent = "Could not load details (" + e.message + ")"; dBody.appendChild(p); } })
        .then(function () { if (my === loadSeq) host.classList.remove("loading"); });
      row.scrollIntoView({ block: "nearest" });
      if (focus) row.focus({ preventScroll: true });
    }
    function closeDetail() {
      if (!host || !dBody || !dBody.innerHTML.trim()) return false;
      dBody.innerHTML = ""; if (dDef) dDef.hidden = false;
      detailRows().forEach(function (r) { r.classList.remove("sel"); });
      setSelParam(null);
      return true;
    }
    function step(d) {
      var rows = detailRows(); if (!rows.length) return;
      var i = rows.findIndex(function (r) { return r.classList.contains("sel"); });
      var n = rows[Math.max(0, Math.min(rows.length - 1, i < 0 ? 0 : i + d))];
      if (n) openDetail(n, true);
    }
    if (host) host.addEventListener("click", function (e) {
      if (e.target.closest("[data-prev]")) step(-1);
      else if (e.target.closest("[data-next]")) step(1);
      else if (e.target.closest("[data-dclose]")) closeDetail();
    });
    // rows: click = details (with a panel) or open the link; double-click = open the full page
    document.addEventListener("click", function (e) {
      var tr = e.target.closest("tr[data-href],tr[data-detail]");
      if (!tr || e.target.closest("a,button,input,label,select,textarea")) return;
      if (tr.dataset.detail && host) openDetail(tr, false);
      else if (tr.dataset.href) location.href = tr.dataset.href;
    });
    document.addEventListener("dblclick", function (e) {
      var tr = e.target.closest("tr[data-detail][data-href]");
      if (tr && host && !e.target.closest("a,button,input,label,select,textarea")) location.href = tr.dataset.href;
    });
    document.addEventListener("keydown", function (e) {
      var tr = e.target.closest && e.target.closest("tr[tabindex]");
      if (!tr || typing(e)) return;
      var rows = Array.prototype.slice.call(tr.closest("tbody").querySelectorAll("tr[tabindex]")).filter(function (r) { return !r.hidden; });
      var i = rows.indexOf(tr);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        var n = rows[i + (e.key === "ArrowDown" ? 1 : -1)];
        if (!n) return;
        if (n.dataset.detail && host) openDetail(n, true); else n.focus();
      } else if (e.key === "Enter") {
        if (tr.dataset.detail && host && !tr.classList.contains("sel")) openDetail(tr, true);
        else if (tr.dataset.href) location.href = tr.dataset.href;
      }
    });
    // Esc: close the toast, then the details, then a floating panel
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || (modal && modal.open) || (menu && menu.open)) return;
      var t = document.querySelector("[data-toast]");
      if (t) { closeMsg(t); return; }
      if (typing(e)) return;
      if (closeDetail()) return;
      if (side && overlayMq.matches && !isClosed()) setClosed(true, false);
    });

    // ------------------------------------------------------------ collapsible trees (table.tree-t)
    document.querySelectorAll("table.tree-t").forEach(function (table) {
      var key = "mcx-tree:" + location.pathname + ":" + table.id;
      var rows = Array.prototype.slice.call(table.querySelectorAll("tbody tr[data-level]"));
      var byNode = {};
      rows.forEach(function (r) { if (r.dataset.node) byNode[r.dataset.node] = r; });
      var openSet = {};
      try { (JSON.parse(stored(key) || "[]") || []).forEach(function (n) { openSet[n] = 1; }); } catch (e) {}
      rows.forEach(function (r) { if (r.dataset.level === "1") r.setAttribute("aria-expanded", openSet[r.dataset.node] ? "true" : "false"); });
      var fq = document.querySelector('[data-tree-filter="' + table.id + '"]');
      var fs = document.querySelector('[data-tree-show="' + table.id + '"]');
      var none = table.querySelector(".tree-none");
      function save() {
        var open = rows.filter(function (r) { return r.dataset.level === "1" && r.getAttribute("aria-expanded") === "true"; })
          .map(function (r) { return r.dataset.node; });
        store(key, JSON.stringify(open));
      }
      function refresh() {
        var q = fq ? fq.value.trim().toLowerCase() : "", mode = fs ? fs.value : "";
        var objOk = {}, colHit = {}, anyObj = false;
        rows.forEach(function (r) {                          // which objects pass, which columns match
          if (r.dataset.level === "2" && q && (r.dataset.text || "").indexOf(q) >= 0) colHit[r.dataset.parent] = true;
        });
        rows.forEach(function (r) {
          if (r.dataset.level !== "1") return;
          var st = r.dataset.state, pii = parseInt(r.dataset.pii || "0", 10);
          var modeOk = !mode || (mode === "pii" ? pii > 0 : st === mode);
          var textOk = !q || (r.dataset.text || "").indexOf(q) >= 0 || colHit[r.dataset.node];
          objOk[r.dataset.node] = modeOk && textOk;
          r.classList.toggle("hit", !!q && (r.dataset.text || "").indexOf(q) >= 0);
        });
        var srcHas = {};
        rows.forEach(function (r) {
          var lvl = r.dataset.level, show = true;
          if (lvl === "1") {
            show = objOk[r.dataset.node];
            if (show) { anyObj = true; if (r.dataset.parent) srcHas[r.dataset.parent] = true; }
            var src = r.dataset.parent && byNode[r.dataset.parent];
            if (src && src.getAttribute("aria-expanded") === "false") show = false;
          } else if (lvl === "2") {
            var par = byNode[r.dataset.parent];
            var hit = !!q && (r.dataset.text || "").indexOf(q) >= 0;
            var parentNameHit = par && q && (par.dataset.text || "").indexOf(q) >= 0;
            show = objOk[r.dataset.parent] && (par.getAttribute("aria-expanded") === "true" || hit) && (!q || hit || parentNameHit);
            if (par && par.parentNode && byNode[par.dataset.parent] && byNode[par.dataset.parent].getAttribute("aria-expanded") === "false") show = false;
            r.classList.toggle("hit", hit);
          }
          r.hidden = !show;
        });
        rows.forEach(function (r) { if (r.dataset.level === "0") r.hidden = !srcHas[r.dataset.node] && !!(q || mode); });
        if (none) none.hidden = anyObj || !(q || mode);
      }
      function toggle(r, open) {
        if (!r || !r.hasAttribute("aria-expanded")) return;
        if (open === undefined) open = r.getAttribute("aria-expanded") !== "true";
        r.setAttribute("aria-expanded", open ? "true" : "false");
        refresh(); save();
      }
      table.addEventListener("click", function (e) {
        var b = e.target.closest(".tw"), r = e.target.closest("tr[data-level]");
        if (!r) return;
        if (b) { e.stopPropagation(); toggle(r); return; }
        if (r.dataset.detail && host) return;              // the row opens its details instead
        if (r.dataset.level !== "2" && !e.target.closest("a,button,input,label,select")) toggle(r);
      }, true);
      table.addEventListener("keydown", function (e) {
        var r = e.target.closest("tr[data-level]");
        if (!r || typing(e)) return;
        if (e.key === "ArrowRight") { e.preventDefault(); toggle(r, true); }
        else if (e.key === "ArrowLeft") { e.preventDefault(); toggle(r, false); }
        else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !r.dataset.detail) {
          e.preventDefault();
          var vis = rows.filter(function (x) { return !x.hidden && x.hasAttribute("tabindex"); });
          var n = vis[vis.indexOf(r) + (e.key === "ArrowDown" ? 1 : -1)];
          if (n) n.focus();
        }
      });
      if (fq) fq.addEventListener("input", refresh);
      if (fs) fs.addEventListener("change", refresh);
      document.querySelectorAll('[data-tree-all="' + table.id + '"]').forEach(function (b) {
        b.addEventListener("click", function () {
          var open = b.dataset.open === "1";
          rows.forEach(function (r) { if (r.dataset.level === "1") r.setAttribute("aria-expanded", open ? "true" : "false"); });
          refresh(); save();
        });
      });
      document.querySelectorAll('[data-tree-clear="' + table.id + '"]').forEach(function (b) {
        b.addEventListener("click", function () { if (fq) fq.value = ""; if (fs) fs.value = ""; refresh(); });
      });
      refresh();
    });

    // ------------------------------------------------------------ unsaved changes (Safety: personal data)
    document.querySelectorAll("form[data-dirty-form]").forEach(function (form) {
      var boxes = Array.prototype.slice.call(form.querySelectorAll("input[type=checkbox]"));
      var initial = boxes.map(function (b) { return b.checked; });
      var note = form.querySelector("[data-dirty-note]"), submitting = false;
      function count() { return boxes.filter(function (b, i) { return b.checked !== initial[i]; }).length; }
      form.addEventListener("change", function (e) {
        var i = boxes.indexOf(e.target);
        if (i >= 0) { var tr = e.target.closest("tr"); if (tr) tr.classList.toggle("sel", e.target.checked !== initial[i]); }
        var n = count();
        if (note) { note.hidden = !n; note.textContent = "● " + n + " unsaved change" + (n === 1 ? "" : "s"); }
      });
      form.addEventListener("submit", function () { submitting = true; });
      window.addEventListener("beforeunload", function (e) { if (count() && !submitting) { e.preventDefault(); e.returnValue = ""; } });
      document.addEventListener("click", function (e) {
        var a = e.target.closest("a[href]");
        if (!a || !count() || submitting || a.target || a.href.indexOf("#") === location.href.length) return;
        e.preventDefault();
        ask({ title: "Leave without saving?", message: count() + " personal-data change(s) are not saved yet.", ok: "Leave", danger: true },
          function () { submitting = true; location.href = a.href; });
      });
    });

    // ------------------------------------------------------------ sidebar: expanded / collapsed (Ctrl B)
    var navBtn = document.getElementById("nav-btn");
    function navNow() { return root.classList.contains("nav-icons") ? "icons" : "full"; }
    function setNav(m, save) {
      root.classList.toggle("nav-icons", m !== "full");
      if (save !== false) store("mcx-nav-mode", m);
      if (navBtn) {
        navBtn.setAttribute("aria-expanded", m === "full" ? "true" : "false");
        var l = navBtn.querySelector(".nl"); if (l) l.textContent = m === "full" ? "Collapse" : "Expand";
        navBtn.title = (m === "full" ? "Shrink the sidebar to icons" : "Show the sidebar labels") + " ( Ctrl B )";
      }
      if (side) setW(chosen, false);                       // the page got wider / narrower: re-fit the right panel
    }
    if (navBtn) { setNav(navNow(), false); navBtn.addEventListener("click", function () { setNav(navNow() === "full" ? "icons" : "full"); }); }
    document.addEventListener("keydown", function (e) {      // Ctrl B: labels <-> icons (the section menu stays)
      if ((e.key === "b" || e.key === "B") && (e.ctrlKey || e.metaKey) && !e.shiftKey) {
        e.preventDefault(); setNav(navNow() === "full" ? "icons" : "full");
      }
    });

    // ------------------------------------------------------------ bulk selection (tick rows -> bar)
    document.querySelectorAll("[data-bulkbar]").forEach(function (bar) {
      var id = bar.dataset.bulkbar, all = document.querySelector('[data-check-all="' + id + '"]');
      var label = bar.querySelector("[data-bulk-count]");
      function boxes() { return Array.prototype.slice.call(document.querySelectorAll('input[form="' + id + '"][name="tool"]')); }
      function update() {
        var n = boxes().filter(function (b) { return b.checked; }).length;
        if (n) bar.removeAttribute("data-empty"); else bar.setAttribute("data-empty", "");
        if (label) label.textContent = n + " selected";
        if (all) { all.checked = n && n === boxes().length; all.indeterminate = n > 0 && n < boxes().length; }
      }
      document.addEventListener("change", function (e) {
        if (e.target === all) boxes().forEach(function (b) { b.checked = all.checked; });
        if (e.target === all || boxes().indexOf(e.target) >= 0) update();
      });
      update();
    });

    // ------------------------------------------------------------ help strips: closing one is remembered per page
    document.querySelectorAll("[data-help]").forEach(function (h) {
      if (stored("mcx-help:" + h.dataset.help) === "0") h.hidden = true;
      var x = h.querySelector("[data-help-close]");
      if (x) x.addEventListener("click", function () { h.hidden = true; store("mcx-help:" + h.dataset.help, "0"); });
    });

    // ------------------------------------------------------------ drawer: add / edit forms from the right
    var dwBack = null, dw = null, dwDirty = false, dwFrom = null, dwUrl = "", checkTimer = null;
    function dwForm() { return dw && dw.querySelector("form[data-dw-form]"); }
    function openDrawer(url, from) {
      if (!dw) {
        dwBack = document.createElement("div"); dwBack.className = "dw-back";
        dw = document.createElement("div"); dw.className = "dw"; dw.setAttribute("role", "dialog"); dw.setAttribute("aria-modal", "true");
        document.body.appendChild(dwBack); document.body.appendChild(dw);
        dwBack.addEventListener("click", function () { closeDrawer(false); });
        dw.addEventListener("click", drawerClick);
        dw.addEventListener("input", drawerEdit); dw.addEventListener("change", drawerEdit);
        dw.addEventListener("submit", function (e) {
          var f = e.target, more = dw.querySelector("[data-dw-more]"), back = f.querySelector('input[name="back"]');
          if (more && more.checked && back) {
            var q = new URLSearchParams(location.search); q.set("drawer", dwUrl);
            back.value = location.pathname + "?" + q;
          }
          dwDirty = false;
        });
      }
      dwFrom = from || document.activeElement;
      dwUrl = url;
      var u = url + (url.indexOf("back=") < 0 ? (url.indexOf("?") < 0 ? "?" : "&") + "back=" + encodeURIComponent(location.pathname + location.search) : "");
      dw.innerHTML = '<div class="dw-h"><div class="dw-t"><div class="title">Loading…</div></div></div>';
      dw.setAttribute("aria-label", "Edit");
      requestAnimationFrame(function () { root.classList.add("dw-open"); });
      fetch(u, { credentials: "same-origin" })
        .then(function (r) { if (!r.ok && r.status !== 404) throw new Error("HTTP " + r.status); return r.text(); })
        .then(function (html) {
          dw.innerHTML = html;                                 // server-rendered, escaped by Jinja, same origin
          dwDirty = false;
          var t = dw.querySelector(".dw-t .title"); if (t) dw.setAttribute("aria-label", t.textContent);
          var first = dw.querySelector(".dw-b input:not([type=hidden]):not([disabled]), .dw-b textarea:not([disabled]), .dw-b select");
          if (first) first.focus({ preventScroll: true });
          runCheck();
        })
        .catch(function (e) { dw.innerHTML = ""; var pe = document.createElement("p"); pe.className = "pb bad"; pe.textContent = "Could not open the form (" + e.message + ")"; dw.appendChild(pe); });
    }
    function closeDrawer(force) {
      if (!dw || !root.classList.contains("dw-open")) return;
      if (dwDirty && !force) {
        ask({ title: "Discard changes?", message: "Your changes in this form are not saved.", ok: "Discard", danger: true },
          function () { closeDrawer(true); });
        return;
      }
      root.classList.remove("dw-open"); dwDirty = false;
      setTimeout(function () { if (!root.classList.contains("dw-open")) dw.innerHTML = ""; }, 220);
      if (dwFrom && dwFrom.focus) dwFrom.focus({ preventScroll: true });
    }
    function runCheck() {
      var f = dwForm();
      if (!f || !f.dataset.check) return;
      clearTimeout(checkTimer);
      checkTimer = setTimeout(function () {
        var out = dw.querySelector("[data-dw-check]"), sig = dw.querySelector("[data-dw-sig]");
        post(f.dataset.check, new FormData(f)).then(function (r) {
          if (!out) return;
          out.textContent = (r.ok ? "✓ " : "✕ ") + r.text; out.className = "dw-check small " + (r.ok ? "ok" : "bad");
          if (sig && r.sig) sig.textContent = r.sig;
        }).catch(function () {});
      }, 350);
    }
    function drawerEdit(e) {
      if (e.target.closest("[data-dw-args]")) return;
      dwDirty = true;
      var k = e.target.closest("[data-dw-kind]");
      if (k) k.closest(".prow").dataset.kind = k.value;
      runCheck();
    }
    function drawerClick(e) {
      var t = e.target;
      if (t.closest("[data-dw-close]")) { closeDrawer(false); return; }
      var tab = t.closest("[data-dw-tab]");
      if (tab) {
        var mode = tab.dataset.dwTab;
        dw.querySelectorAll("[data-dw-tab]").forEach(function (b) { b.classList.toggle("on", b === tab); });
        dw.querySelectorAll("[data-dw-pane]").forEach(function (p) { p.hidden = p.dataset.dwPane !== mode; });
        var ta = dw.querySelector('textarea[name="params_yaml"]'); if (ta) ta.disabled = mode !== "yaml";
        var mi = dw.querySelector("[data-dw-mode]"); if (mi) mi.value = mode;
        runCheck(); return;
      }
      var add = t.closest("[data-dw-add]");
      if (add) {
        var kind = add.dataset.dwAdd, cnt = dw.querySelector('[data-dw-count="' + kind + '"]');
        var tpl = dw.querySelector('template[data-dw-tpl="' + kind + '"]'), list = dw.querySelector('[data-dw-list="' + kind + '"]');
        if (!cnt || !tpl || !list) return;
        var i = parseInt(cnt.value, 10) || 0;
        var box = document.createElement("div"); box.innerHTML = tpl.innerHTML.replace(/__i__/g, String(i));
        var row = box.firstElementChild; list.appendChild(row); cnt.value = String(i + 1);
        var inp = row.querySelector("input,select"); if (inp) inp.focus();
        dwDirty = true; return;
      }
      if (t.closest("[data-dw-test]")) {
        var tb2 = dw.querySelector("[data-dw-test-box]"); if (tb2) { tb2.hidden = !tb2.hidden; if (!tb2.hidden) tb2.scrollIntoView({ block: "nearest" }); }
        return;
      }
      if (t.closest("[data-dw-run]")) {
        var f = dwForm(), o = dw.querySelector("[data-dw-out]"), a = dw.querySelector("[data-dw-args]");
        o.textContent = "Running the saved draft…";
        post(f.dataset.test, { args: a ? a.value : "" }).then(function (r) {
          o.textContent = (r.error ? "✕ " + r.error + "\n\n" : "") + JSON.stringify(r.env, null, 2);
        }).catch(function (er) { o.textContent = "✕ Request failed (" + er.message + ")"; });
      }
    }
    document.addEventListener("click", function (e) {
      var el = e.target.closest("[data-drawer]");
      if (!el || (dw && dw.contains(el) && !el.closest(".dw-b"))) return;
      e.preventDefault();
      openMenus().forEach(function (m) { m.open = false; });
      openDrawer(el.dataset.drawer, el);
    });
    document.addEventListener("keydown", function (e) {
      if (!dw || !root.classList.contains("dw-open")) {
        var row = e.target.closest && e.target.closest("tr[data-edit]");     // E on a focused row = edit
        if (row && (e.key === "e" || e.key === "E") && !typing(e) && !e.ctrlKey && !e.metaKey) { e.preventDefault(); openDrawer(row.dataset.edit, row); }
        return;
      }
      if (modal && modal.open) return;
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeDrawer(false); }
      else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { var f = dwForm(); if (f) { e.preventDefault(); f.requestSubmit ? f.requestSubmit() : f.submit(); } }
      else if (e.key === "Tab") {                                       // keep focus inside the drawer
        var items = Array.prototype.slice.call(dw.querySelectorAll("a[href],button:not([disabled]),input:not([type=hidden]):not([disabled]),select,textarea:not([disabled])"))
          .filter(function (x) { return x.offsetParent !== null; });
        if (!items.length) return;
        if (e.shiftKey && document.activeElement === items[0]) { e.preventDefault(); items[items.length - 1].focus(); }
        else if (!e.shiftKey && document.activeElement === items[items.length - 1]) { e.preventDefault(); items[0].focus(); }
      }
    }, true);
    var openParam = new URLSearchParams(location.search).get("drawer");   // "Create more": reopen after saving
    if (openParam && openParam.charAt(0) === "/" && openParam.indexOf("/drawer/") === 0) {
      var q1 = new URLSearchParams(location.search); q1.delete("drawer");
      history.replaceState && history.replaceState(null, "", location.pathname + (q1.toString() ? "?" + q1 : ""));
      openDrawer(openParam, null);
    }

    // ------------------------------------------------------------ flow view: which step stopped a test call
    function flowResult(r) {
      var flow = document.querySelector("[data-flow]");
      if (!flow) return;
      var err = (r.error || "").toLowerCase(), fail = 0;
      var CODE_STEP = { not_listed: 1, risk: 1, scope: 1, unknown_tool: 1, bad_argument: 2, range: 3, rate: 3,
                        rows: 3, timeout: 3, allow_list: 4, config: 4, source: 4 };
      var code = r.env && r.env.code;
      if (err && code && CODE_STEP[code]) fail = CODE_STEP[code];
      else if (err) {
        if (/profile|not listed|not available|disabled|licence/.test(err)) fail = 1;
        else if (/argument|required|must be|not a valid|invalid|unknown|expected/.test(err)) fail = 2;
        else if (/rate|range|days|timeout|limit|too many|max /.test(err)) fail = 3;
        else fail = 4;
      }
      flow.querySelectorAll(".fnode").forEach(function (n) {
        var st = parseInt(n.dataset.step, 10), lab = n.querySelector("[data-fst]");
        n.classList.remove("pass", "fail", "skip");
        if (!fail || st < fail) { n.classList.add("pass"); if (lab) lab.textContent = st === 4 ? "✓ " + r.ms + " ms" : st === 5 ? "✓ " + ((r.env && r.env.rows) ? r.env.rows.length : 0) + " rows" : "✓"; }
        else if (st === fail) { n.classList.add("fail"); if (lab) lab.textContent = "✕ " + r.error.slice(0, 60); }
        else { n.classList.add("skip"); if (lab) lab.textContent = ""; }
      });
    }

    // ------------------------------------------------------------ test call (tool page / source step 3)
    var tf = document.getElementById("test-form");
    if (tf) tf.addEventListener("submit", function (e) {
      e.preventDefault();
      var out = document.getElementById("test-out"), note = document.getElementById("test-note"), btn = tf.querySelector("button");
      btn.disabled = true; note.textContent = "Running…";
      post(tf.action, { args: tf.querySelector("input[name=args]").value }).then(function (r) {
        btn.disabled = false;
        out.textContent = JSON.stringify(r.env, null, 2);
        var n = (r.env && r.env.rows) ? r.env.rows.length : 0;
        note.textContent = (r.error ? "✕ " + r.error : n + " rows" + (r.env.truncated ? " · truncated" : "")) +
          " · " + r.ms + " ms · audited as test call by " + (r.user || "you");
        note.className = r.error ? "small bad" : "small muted";
        out.hidden = false;
        flowResult(r);
      }).catch(function (e) { btn.disabled = false; note.textContent = "✕ Request failed (" + e.message + ")"; note.className = "small bad"; });
    });

    // small JSON actions: test connection, test endpoint
    document.addEventListener("click", function (e) {
      var b = e.target.closest("[data-post-json]");
      if (!b) return;
      (function () {
        var out = document.getElementById(b.dataset.out);
        out.textContent = "Testing…";
        post(b.dataset.postJson).then(function (r) {
          if (r.text) { out.textContent = r.text; out.className = r.ok ? "small muted" : "small bad"; return; }
          out.textContent = r.state === "online" ? "✓ Connected · " + r.latency + " ms" : "✕ " + (r.message || r.state);
          out.className = r.state === "online" ? "small ok" : "small bad";
        }).catch(function (e) { out.textContent = "✕ Request failed (" + e.message + ")"; out.className = "small bad"; });
      })();
    });

    // live status: footer, MCP chip, publish counter (every 5 s)
    var foot = document.getElementById("live");
    if (foot) setInterval(function () {
      fetch("/api/status", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (s) {
        if (!s) return;
        foot.textContent = s.clients + " clients · " + s.calls_today.toLocaleString() + " calls today · " + s.blocked_today + " blocked";
        var chip = document.getElementById("mcp-chip");
        if (chip) {
          chip.className = "chip " + (s.server ? "s-ok" : "s-bad");
          var l = chip.querySelector(".lbl"); if (l) l.textContent = s.server ? " MCP ready" : " MCP stopped";
          var d = chip.querySelector(".dot"); if (d) d.className = "dot " + (s.server ? "ok" : "bad");
        }
        var pc = document.getElementById("pub-count"); if (pc) pc.textContent = s.pending ? "(" + s.pending + ")" : "";
      }).catch(function () {});
    }, 5000);
  });
})();
