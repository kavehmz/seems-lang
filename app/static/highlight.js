// Syntax colouring for Seems source. Python tokens are found here. The Seems words
// (verbs, English phrases, declarations) come from the server as "marks", so the
// colours always agree with the real parser.
(function () {
  const KEYWORDS = new Set(("False None True and as assert async await break class continue def del elif else " +
    "except finally for from global if import in is lambda nonlocal not or pass raise return try while with " +
    "yield match case").split(" "));
  const STRING_START = /^[rRbBfFuU]{0,2}("""|'''|"|')/;
  const IDENT = /^[A-Za-z_][A-Za-z0-9_]*/;
  const NUMBER = /^\d[\d_]*(\.\d+)?([eE][+-]?\d+)?/;

  function escapeHtml(text) {
    return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function paint(cls, from, to, name) {
    for (let i = from; i < to && i < cls.length; i++) cls[i] = name;
  }

  function closeString(line, from, quote) {
    let i = from;
    while (i < line.length) {
      if (line[i] === "\\") { i += 2; continue; }
      if (line.startsWith(quote, i)) return i + quote.length;
      i++;
    }
    return -1;
  }

  // Returns an array with one HTML string per line.
  function highlightLines(source, marks) {
    const byLine = new Map();
    for (const m of marks || []) {
      if (!byLine.has(m[0])) byLine.set(m[0], []);
      byLine.get(m[0]).push(m);
    }
    let triple = null;
    return source.split("\n").map((line, index) => {
      const cls = new Array(line.length).fill("");
      let i = 0;
      while (i < line.length) {
        if (triple) {
          const end = closeString(line, i, triple);
          if (end < 0) { paint(cls, i, line.length, "str"); i = line.length; break; }
          paint(cls, i, end, "str"); i = end; triple = null; continue;
        }
        const rest = line.slice(i);
        if (rest[0] === "#") { paint(cls, i, line.length, "com"); break; }
        let m = STRING_START.exec(rest);
        if (m) {
          const quote = m[1], bodyStart = i + m[0].length;
          const end = closeString(line, bodyStart, quote);
          if (end < 0) {
            paint(cls, i, line.length, "str");
            if (quote.length === 3) triple = quote;
            i = line.length; break;
          }
          paint(cls, i, end, "str"); i = end; continue;
        }
        m = IDENT.exec(rest);
        if (m) {
          if (KEYWORDS.has(m[0])) paint(cls, i, i + m[0].length, "kw");
          else if (rest[m[0].length] === "(") paint(cls, i, i + m[0].length, "fn");
          i += m[0].length; continue;
        }
        m = NUMBER.exec(rest);
        if (m) { paint(cls, i, i + m[0].length, "num"); i += m[0].length; continue; }
        i++;
      }
      for (const mark of byLine.get(index + 1) || []) {
        if (mark[3] === "subject" || mark[3] === "value") continue;
        paint(cls, mark[1], mark[2], "sx-" + mark[3]);
      }
      let html = "", start = 0;
      for (let k = 1; k <= line.length; k++) {
        if (k === line.length || cls[k] !== cls[start]) {
          const piece = escapeHtml(line.slice(start, k));
          html += cls[start] ? `<span class="${cls[start]}">${piece}</span>` : piece;
          start = k;
        }
      }
      return html;
    });
  }

  window.SeemsHighlight = { highlightLines, escapeHtml };
})();
