// Seems playground.
(function () {
  const $ = (id) => document.getElementById(id);
  const { highlightLines, escapeHtml } = window.SeemsHighlight;
  const { renderJudgment, pipClass, el } = window.SeemsTrace;

  const src = $("src"), hl = $("hl"), gutter = $("gutter"), problem = $("problem");
  const output = $("output"), judgments = $("judgments"), stats = $("stats"), python = $("python");
  const runButton = $("run"), runState = $("runState");

  const state = {
    marks: [], errorLine: 0, pips: new Map(), flashLine: 0,
    requests: new Map(), rows: new Map(), running: null, pricePerMtok: 0.042,
    totals: null,
  };

  // ---------------------------------------------------------------- editor --- //

  function paint() {
    const lines = highlightLines(src.value, state.marks);
    hl.innerHTML = lines.map((html, i) =>
      (i + 1 === state.flashLine ? `<span class="line-flash">${html || " "}</span>` : html)).join("\n") + "\n";
    const rows = [];
    for (let n = 1; n <= lines.length; n++) {
      const pips = (state.pips.get(n) || []).slice(-4).map((c) => `<span class="pip ${c}"></span>`).join("");
      const cls = n === state.errorLine ? "err" : n === state.flashLine ? "flash" : "";
      rows.push(`<div class="${cls}">${pips}<span class="n">${n}</span></div>`);
    }
    gutter.innerHTML = rows.join("");
    syncScroll();
  }

  function syncScroll() {
    hl.scrollTop = src.scrollTop;
    hl.scrollLeft = src.scrollLeft;
    gutter.scrollTop = src.scrollTop;
  }

  let checkTimer = 0, checkToken = 0;
  function scheduleCheck() {
    clearTimeout(checkTimer);
    checkTimer = setTimeout(check, 220);
  }

  async function check() {
    const token = ++checkToken, source = src.value;
    try {
      const reply = await post("/api/translate", { source });
      if (token !== checkToken) return;
      if (reply.ok) {
        state.marks = reply.marks; state.errorLine = 0; problem.hidden = true;
        showPython(reply.python, reply.changed);
      } else {
        state.errorLine = reply.error.line;
        problem.hidden = false;
        problem.textContent = `Line ${reply.error.line}: ${reply.error.message}` + (reply.error.hint ? `. ${reply.error.hint}.` : "");
      }
      paint();
    } catch (e) { /* the server is restarting; the next keystroke tries again */ }
  }

  function edited() {
    state.pips.clear(); state.flashLine = 0;
    paint(); scheduleCheck();
  }

  // Only a program the visitor typed is kept for the next visit. Examples are not.
  function saveDraft(text) {
    try {
      if (text === null) localStorage.removeItem("seems.draft");
      else localStorage.setItem("seems.draft", text);
    } catch (e) { /* private mode */ }
  }

  src.addEventListener("input", () => {
    edited(); saveDraft(src.value);
    $("fileName").textContent = "main.seems";
    document.querySelectorAll(".ex.on").forEach((b) => b.classList.remove("on"));
  });
  src.addEventListener("scroll", syncScroll);
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") { event.preventDefault(); run(); }
  });
  src.addEventListener("keydown", (event) => {
    if (event.metaKey || event.ctrlKey) return;
    const start = src.selectionStart, end = src.selectionEnd, text = src.value;
    if (event.key === "Tab") {
      event.preventDefault();
      insert("    ");
    } else if (event.key === "Enter") {
      event.preventDefault();
      const lineStart = text.lastIndexOf("\n", start - 1) + 1;
      const line = text.slice(lineStart, start);
      let indent = (/^[ \t]*/.exec(line) || [""])[0];
      if (/:\s*(#.*)?$/.test(line) || /^\s*(kind|scale|judgment)\b.*"\s*$/.test(line)) indent += "    ";
      insert("\n" + indent);
    } else if (event.key === "Backspace" && start === end) {
      const lineStart = text.lastIndexOf("\n", start - 1) + 1;
      const before = text.slice(lineStart, start);
      if (before.length >= 4 && /^ +$/.test(before) && before.length % 4 === 0) {
        event.preventDefault();
        src.setSelectionRange(start - 4, start);
        insert("");
      }
    }
  });

  function insert(text) {
    // execCommand keeps the browser's undo history working.
    if (!document.execCommand("insertText", false, text)) {
      const start = src.selectionStart, end = src.selectionEnd;
      src.value = src.value.slice(0, start) + text + src.value.slice(end);
      src.setSelectionRange(start + text.length, start + text.length);
      edited(); saveDraft(src.value);
    }
  }

  function flash(line) {
    state.flashLine = line;
    paint();
    const lineHeight = parseFloat(getComputedStyle(src).lineHeight) || 21;
    const target = (line - 1) * lineHeight - src.clientHeight / 2;
    src.scrollTop = Math.max(0, target);
    syncScroll();
  }

  // --------------------------------------------------------------- results --- //

  function showPython(code, changed) {
    const set = new Set(changed || []);
    const lines = highlightLines(code, []);
    python.innerHTML = lines.map((html, i) =>
      `<div class="row${set.has(i + 1) ? " changed" : ""}"><span class="n">${i + 1}</span><span class="c">${html || " "}</span></div>`).join("");
  }

  function resetResults() {
    output.textContent = "";
    judgments.textContent = "";
    state.requests.clear(); state.rows.clear(); state.pips.clear(); state.flashLine = 0;
    state.totals = { judgments: 0, cached: 0, ahead: 0, requests: 0, tokens: 0, unsure: 0, wall: null };
    showStats();
  }

  function showStats() {
    const t = state.totals;
    if (!t) { stats.textContent = ""; return; }
    const cost = t.tokens * state.pricePerMtok / 1e6;
    const parts = [
      ["judgments", t.judgments], ["unsure", t.unsure], ["requests to Jev", t.requests],
      ["from cache", t.cached], ["input tokens", t.tokens.toLocaleString()],
      ["cost", "$" + cost.toFixed(6)],
    ];
    if (t.wall !== null) parts.push(["time", (t.wall / 1000).toFixed(2) + " s"]);
    stats.innerHTML = parts.map(([k, v]) => `<span><b>${v}</b> ${k}</span>`).join("") +
      '<span class="key"><i class="k-no"></i>no <i class="k-unsure"></i>unsure <i class="k-yes"></i>yes</span>';
  }

  function write(text, isError) {
    const span = document.createElement("span");
    if (isError) span.className = "e";
    span.textContent = text;
    output.append(span);
    output.scrollTop = output.scrollHeight;
  }

  function onTrace(event) {
    const t = state.totals;
    if (event.type === "request") {
      state.requests.set(event.id, event);
      t.requests += 1; t.tokens += event.input_tokens || 0;
    } else if (event.type === "judgment") {
      const row = renderJudgment(event, state.requests);
      if (event.ahead) row.classList.add("dim");
      row.dataset.pip = pipClass(event);
      row.addEventListener("click", () => flash(Number(row.dataset.line)));
      judgments.append(row);
      state.rows.set(event.id, row);
      t.judgments += 1;
      if (event.cached) t.cached += 1;
      if (event.verdict === "unsure") t.unsure += 1;
      if (!state.pips.has(event.line)) state.pips.set(event.line, []);
      state.pips.get(event.line).push(pipClass(event));
      paint();
    } else if (event.type === "used") {
      const row = state.rows.get(event.id);
      if (row) {
        row.classList.remove("dim");
        const tag = row.querySelector(".ahead-tag");
        if (tag) tag.textContent = "asked ahead, used";
        if (event.line && Number(row.dataset.line) !== event.line) {  // show it on the elif line that used it
          const from = state.pips.get(Number(row.dataset.line)) || [];
          const pip = from.splice(from.lastIndexOf(row.dataset.pip), 1)[0] || row.dataset.pip;
          if (!state.pips.has(event.line)) state.pips.set(event.line, []);
          state.pips.get(event.line).push(pip);
          row.dataset.line = event.line;
          row.querySelector(".j-line").textContent = "L" + event.line;
          paint();
        }
      }
    }
    showStats();
  }

  function onMessage(message) {
    if (message.t === "out") write(message.data, false);
    else if (message.t === "err") write(message.data, true);
    else if (message.t === "trace") onTrace(message.event);
    else if (message.t === "python") showPython(message.code, message.changed);
    else if (message.t === "syntax") {
      write(`Syntax error on line ${message.line}: ${message.message}` + (message.hint ? `. ${message.hint}.` : "") + "\n", true);
      state.errorLine = message.line; paint();
    } else if (message.t === "done") {
      state.totals.wall = message.ms;
      showStats();
      runState.textContent = message.code === 0 ? `finished in ${(message.ms / 1000).toFixed(2)} s` : `stopped with an error`;
      if (!judgments.children.length) {
        judgments.append(el("div", "empty", "This run asked Jev nothing. Exact conditions decided everything, or the program has no judgments."));
      }
    }
  }

  async function run() {
    if (state.running) { state.running.abort(); return; }
    resetResults();
    paint();
    const controller = new AbortController();
    state.running = controller;
    runButton.textContent = "Stop";
    runState.textContent = "running…";
    try {
      const response = await fetch("/api/run", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
        body: JSON.stringify({ source: src.value, cache: $("cache").checked, sure: Number($("sure").value) }),
      });
      if (!response.ok || !response.body) throw new Error(`server answered ${response.status}`);
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) if (line.trim()) onMessage(JSON.parse(line));
      }
    } catch (error) {
      if (error.name === "AbortError") { write("\n[stopped]\n", true); runState.textContent = "stopped"; }
      else { write(`\nCould not run: ${error.message}\n`, true); runState.textContent = "failed"; }
    } finally {
      state.running = null;
      runButton.textContent = "Run";
    }
  }

  // ------------------------------------------------------------------ setup --- //

  async function post(url, body) {
    const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return response.json();
  }

  function load(source, name) {
    src.value = source;
    $("fileName").textContent = name;
    src.scrollTop = 0;
    state.marks = [];
    edited();
    check();
  }

  async function start() {
    document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t === tab));
      document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("on", p.id === "pane-" + tab.dataset.pane));
    }));
    runButton.addEventListener("click", run);
    $("sure").addEventListener("input", () => { $("sureOut").textContent = Number($("sure").value).toFixed(2); });
    $("clearCache").addEventListener("click", async () => {
      await post("/api/cache/clear", {});
      runState.textContent = "stored answers forgotten";
    });

    try {
      const health = await (await fetch("/api/health")).json();
      $("statusDot").className = "dot " + (health.has_key ? "ok" : "bad");
      $("statusText").textContent = health.has_key ? `Jev connected (${health.model})` : "No API key: set TYPESAFE_API_KEY in .env";
      const language = await (await fetch("/api/language")).json();
      state.pricePerMtok = language.price_per_mtok_usd;
    } catch (e) {
      $("statusDot").className = "dot bad"; $("statusText").textContent = "server not reachable";
    }

    const examples = await (await fetch("/api/examples")).json();
    const strip = $("examples");
    examples.forEach((example, index) => {
      const button = el("button", "ex");
      button.append(el("b", "", `${index + 1}. ${example.title}`), el("small", "", example.blurb.replace(/`/g, "")));
      button.addEventListener("click", () => {
        strip.querySelectorAll(".ex").forEach((b) => b.classList.toggle("on", b === button));
        saveDraft(null);
        load(example.source, example.id + ".seems");
        resetResults(); state.totals = null; showStats();
        output.innerHTML = '<span class="hint">Press Run.</span>';
        judgments.innerHTML = '<div class="empty">Every judgment appears here with its probability, the exact question Jev got, and what it cost.</div>';
      });
      strip.append(button);
    });

    let draft = null;
    try { draft = localStorage.getItem("seems.draft"); localStorage.removeItem("seems.source"); } catch (e) { /* private mode */ }
    if (draft && draft.trim()) load(draft, "main.seems");
    else strip.querySelector(".ex").click();
  }

  start();
})();
