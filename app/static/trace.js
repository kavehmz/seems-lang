// Draws one judgment (a row of the trace). Shared by the playground and the desk.
(function () {
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function bars(probabilities, top) {
    const grid = el("div", "bars");
    for (const [name, p] of Object.entries(probabilities || {})) {
      const isTop = name === top;
      grid.append(el("span", "name" + (isTop ? " best" : ""), name));
      const bar = el("span", "bar" + (isTop ? " best" : ""));
      const fill = el("i");
      fill.style.width = Math.round(p * 100) + "%";
      bar.append(fill);
      grid.append(bar, el("span", "val", p.toFixed(2)));
    }
    return grid;
  }

  function track(p, sure) {
    const line = el("div", "track");
    const no = el("div", "zone z-no"), mid = el("div", "zone z-unsure"), yes = el("div", "zone z-yes");
    no.style.width = (1 - sure) * 100 + "%";
    mid.style.width = (2 * sure - 1) * 100 + "%";
    yes.style.width = (1 - sure) * 100 + "%";
    no.title = "no: at most " + (1 - sure).toFixed(2);
    mid.title = "unsure";
    yes.title = "yes: at least " + sure.toFixed(2);
    const needle = el("div", "needle");
    needle.style.left = p * 100 + "%";
    line.append(no, mid, yes, needle);
    return line;
  }

  function prettyState(text) {
    try { return JSON.stringify(JSON.parse(text), null, 2); } catch (e) { return text; }
  }

  // event: a "judgment" object from the Seems runtime. requests: Map id -> request event.
  function renderJudgment(event, requests) {
    const row = el("div", "j");
    row.dataset.line = event.line;
    row.dataset.id = event.id;
    const head = el("div", "j-head");
    head.append(el("span", "j-line", "L" + event.line), el("code", "j-src", event.source));
    if (event.cached) head.append(el("span", "tag", "from cache"));
    if (event.ahead) {
      const tag = el("span", "tag ahead-tag", "asked ahead");
      tag.title = "A judgment of a later elif branch. It travelled with the if, so the whole statement needs one round trip.";
      head.append(tag);
    }
    let verdict;
    if (event.error) verdict = el("span", "verdict error", "error");
    else if (event.kind === "noul") verdict = el("span", "verdict " + event.verdict, event.verdict + " " + event.p.toFixed(2));
    else if (event.kind === "choice") verdict = el("span", "verdict pick", event.choice);
    else verdict = el("span", "verdict pick", event.level + " " + Number(event.score).toFixed(2));
    head.append(verdict);
    row.append(head);

    if (event.error) row.append(el("div", "j-q", event.error));
    else if (event.kind === "noul") row.append(track(event.p, event.sure || 0.75));
    else row.append(bars(event.probabilities, event.kind === "choice" ? event.choice : event.level));

    const meta = el("div", "j-q j-meta");
    const asked = el("span");
    asked.append("asked: ", el("q", "", event.question));
    meta.append(asked);
    const request = requests && requests.get(event.request);
    if (request) meta.append(el("span", "", `request ${request.id} · ${request.questions} question${request.questions > 1 ? "s" : ""} · ${request.ms} ms`));
    const details = el("details");
    details.append(el("summary", "", "what Jev saw"));
    const question = { type: event.kind, instructions: event.question };
    if (event.criteria) question.criteria = event.criteria;
    const pre = el("pre");
    pre.textContent = "state: " + prettyState(event.state) + "\n\nquestion: " + JSON.stringify(question, null, 2);
    details.append(pre);
    details.addEventListener("click", (e) => e.stopPropagation());
    meta.append(details);
    row.append(meta);
    return row;
  }

  function pipClass(event) {
    if (event.error) return "error";
    return event.kind === "noul" ? event.verdict : event.kind;
  }

  window.SeemsTrace = { renderJudgment, pipClass, el };
})();
