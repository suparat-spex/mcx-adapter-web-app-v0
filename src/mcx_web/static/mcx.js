// MCX Adapter web console - small progressive enhancements (the pages work as plain forms without it).
(function () {
  // theme: saved per browser, follows the system the first time
  var root = document.documentElement;
  var saved = null;
  try { saved = localStorage.getItem("mcx-theme"); } catch (e) {}
  var dark = saved ? saved === "dark" : (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches);
  root.setAttribute("data-theme", dark ? "dark" : "light");

  function csrf() { var m = document.querySelector('meta[name="csrf"]'); return m ? m.content : ""; }
  function post(url, data) {
    var body = new URLSearchParams(data || {}); body.set("csrf", csrf());
    return fetch(url, { method: "POST", body: body, headers: { "X-CSRF": csrf() }, credentials: "same-origin" })
      .then(function (r) { return r.json(); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var tb = document.getElementById("theme-btn");
    function label() { if (tb) tb.lastChild.textContent = root.getAttribute("data-theme") === "dark" ? " Light" : " Dark"; }
    label();
    if (tb) tb.addEventListener("click", function () {
      var d = root.getAttribute("data-theme") !== "dark";
      root.setAttribute("data-theme", d ? "dark" : "light");
      try { localStorage.setItem("mcx-theme", d ? "dark" : "light"); } catch (e) {}
      label();
    });

    // checkboxes / selects that save at once
    document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
      el.addEventListener("change", function () { el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit(); });
    });
    // rows that open a link
    document.querySelectorAll("tr[data-href]").forEach(function (tr) {
      tr.addEventListener("click", function (e) { if (!e.target.closest("a,button,input,label,select")) location.href = tr.dataset.href; });
    });
    // confirm before destructive buttons
    document.querySelectorAll("[data-confirm]").forEach(function (b) {
      b.addEventListener("click", function (e) { if (!confirm(b.dataset.confirm)) e.preventDefault(); });
    });
    // copy buttons
    document.querySelectorAll("[data-copy]").forEach(function (b) {
      b.addEventListener("click", function () {
        var el = document.getElementById(b.dataset.copy);
        var text = el ? (el.value || el.textContent) : b.dataset.copy;
        navigator.clipboard.writeText(text).then(function () { var t = b.textContent; b.textContent = "Copied"; setTimeout(function () { b.textContent = t; }, 1500); });
      });
    });

    // test call
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
      }).catch(function () { btn.disabled = false; note.textContent = "Request failed"; });
    });

    // small JSON actions: test connection, test endpoint
    document.querySelectorAll("[data-post-json]").forEach(function (b) {
      b.addEventListener("click", function () {
        var out = document.getElementById(b.dataset.out);
        out.textContent = "Testing…";
        post(b.dataset.postJson).then(function (r) {
          if (r.text) { out.textContent = r.text; out.className = r.ok ? "small muted" : "small bad"; return; }
          out.textContent = r.state === "online" ? "✓ Connected · " + r.latency + " ms" : "✕ " + (r.message || r.state);
          out.className = r.state === "online" ? "small ok" : "small bad";
        }).catch(function () { out.textContent = "Request failed"; });
      });
    });

    // live status: footer, MCP chip, publish counter (every 5 s)
    var foot = document.getElementById("live");
    if (foot) setInterval(function () {
      fetch("/api/status", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (s) {
        if (!s) return;
        foot.textContent = s.clients + " clients · " + s.calls_today.toLocaleString() + " calls today · " + s.blocked_today + " blocked";
        var chip = document.getElementById("mcp-chip");
        if (chip) { chip.className = "chip " + (s.server ? "s-ok" : "s-bad"); chip.lastChild.textContent = s.server ? " MCP ready" : " MCP stopped"; }
        var pc = document.getElementById("pub-count"); if (pc) pc.textContent = s.pending ? "(" + s.pending + ")" : "";
      }).catch(function () {});
    }, 5000);
  });
})();
