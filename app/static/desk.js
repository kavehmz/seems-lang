// Front end of the support desk. The service behind it is app/desk.seems.
(function () {
  const $ = (id) => document.getElementById(id);
  const { highlightLines } = window.SeemsHighlight;
  const { renderJudgment, el } = window.SeemsTrace;

  const SAMPLES = [
    ["I was charged twice for the annual plan. Please refund the second payment.", 1240],
    ["Our whole team is locked out since 9am and customers cannot order. We lose money every minute.", 0],
    ["THIS IS THE THIRD TIME I WRITE. Your app is garbage and I am leaving.", 39],
    ["The standing desk arrived. It wobbles a bit and I am not sure I want to keep it.", 890],
    ["Hi, my parcel says delivered but it is not here. Could you check with the courier?", 18.5],
    ["Do you have an API for exporting invoices? No rush.", 0],
  ];

  function queueClass(queue) {
    if (queue === "manager") return "queue q-manager";
    if (queue === "priority") return "queue q-priority";
    if (queue === "human review") return "queue q-human";
    return "queue";
  }

  function renderTicket(ticket) {
    const box = el("div", "ticket");
    const top = el("div", "ticket-top");
    top.append(el("span", queueClass(ticket.queue), ticket.queue), el("span", "note", ticket.reason));
    box.append(top, el("div", "ticket-text", ticket.text));
    const facts = el("div", "facts");
    const fact = (label, value) => { const s = el("span"); s.append(label + " ", el("b", "", value)); return s; };
    const requests = ticket.trace.filter((e) => e.type === "request");
    const tokens = requests.reduce((sum, r) => sum + (r.input_tokens || 0), 0);
    facts.append(
      fact("amount", ticket.amount.toFixed(2)), fact("team", `${ticket.team} ${ticket.team_p.toFixed(2)}`),
      fact("anger", `${ticket.anger} ${ticket.anger_score.toFixed(2)}`), fact("asks for refund", ticket.refund_p.toFixed(2)),
      fact("requests", requests.length), fact("tokens", tokens), fact("time", ticket.ms + " ms"));
    box.append(facts);
    const details = el("details");
    const judgments = ticket.trace.filter((e) => e.type === "judgment");
    details.append(el("summary", "", `${judgments.length} judgments`));
    const map = new Map(requests.map((r) => [r.id, r]));
    judgments.forEach((event) => details.append(renderJudgment(event, map)));
    box.append(details);
    return box;
  }

  async function refresh() {
    const tickets = await (await fetch("/desk/api/tickets")).json();
    const pane = $("pane-tickets");
    pane.textContent = "";
    if (!tickets.length) pane.append(el("div", "empty", "No tickets yet. Send one from the left."));
    tickets.forEach((t) => pane.append(renderTicket(t)));
  }

  async function submit(event) {
    event.preventDefault();
    const button = $("send"), note = $("formNote");
    button.disabled = true; button.textContent = "Routing…";
    try {
      const response = await fetch("/desk/api/tickets", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: $("text").value, amount: Number($("amount").value || 0) }),
      });
      const body = await response.json();
      note.textContent = response.ok ? `Routed to "${body.queue}" in ${body.ms} ms.` : `Problem: ${body.error}`;
      if (response.ok) await refresh();
    } catch (error) {
      note.textContent = "Problem: " + error.message;
    } finally {
      button.disabled = false; button.textContent = "Route this ticket";
    }
  }

  async function start() {
    document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t === tab));
      document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("on", p.id === "pane-" + tab.dataset.pane));
    }));
    $("form").addEventListener("submit", submit);
    $("clear").addEventListener("click", async () => { await fetch("/desk/api/tickets", { method: "DELETE" }); refresh(); });
    SAMPLES.forEach(([text, amount]) => {
      const button = el("button", "btn", `${amount ? amount + " · " : ""}${text}`);
      button.type = "button";
      button.addEventListener("click", () => { $("text").value = text; $("amount").value = amount; });
      $("samples").append(button);
    });

    try {
      const health = await (await fetch("/api/health")).json();
      $("statusDot").className = "dot " + (health.has_key ? "ok" : "bad");
      $("statusText").textContent = health.has_key ? `Jev connected (${health.model})` : "No API key: set TYPESAFE_API_KEY in .env";
    } catch (e) { $("statusDot").className = "dot bad"; $("statusText").textContent = "server not reachable"; }

    const file = await (await fetch("/desk/api/source")).json();
    const marks = await fetch("/api/translate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source: file.source }),
    }).then((r) => r.json());
    const changed = new Set(marks.changed || []);
    $("source").innerHTML = highlightLines(file.source, marks.marks || []).map((html, i) =>
      `<div class="row${changed.has(i + 1) ? " changed" : ""}"><span class="n">${i + 1}</span><span class="c">${html || " "}</span></div>`).join("");
    refresh();
  }

  start();
})();
