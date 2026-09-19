"""Build docs/index.html, the language guide that GitHub Pages serves.

    docker compose run --rm app python tools/build_guide.py          # print to stdout
    docker compose run --rm app python tools/build_guide.py --check  # fail if docs/index.html is stale

The page is written in tools/guide_source.html. Code samples sit between `{{seems` and `}}`
lines. Every Seems sample goes through the real translator and the Python compiler, so a sample
that is not valid Seems breaks the build. The colours come from the same parser marks the
playground uses.
"""
from __future__ import annotations

import html
import io
import keyword
import os
import re
import sys
import tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from seems.translator import translate  # noqa: E402

SOURCE = os.path.join(ROOT, "tools", "guide_source.html")
TARGET = os.path.join(ROOT, "docs", "index.html")
BLOCK = re.compile(r"^\{\{(seems|sh|text)\n(.*?)\n\}\}$", re.DOTALL | re.MULTILINE)
STRING_TYPES = {tokenize.STRING} | {getattr(tokenize, name) for name in
                                    ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END") if hasattr(tokenize, name)}


def highlight(source: str) -> str:
    """Seems source -> HTML spans. Raises if the sample is not valid Seems."""
    result = translate(source + "\n", "<guide>")
    compile(result.python, "<guide>", "exec", dont_inherit=True)

    lines = source.split("\n")
    classes = [[""] * len(line) for line in lines]

    def paint(start, end, name):
        (row, col), (end_row, end_col) = start, end
        while row <= end_row and row <= len(lines):
            last = end_col if row == end_row else len(lines[row - 1])
            for i in range(col, min(last, len(lines[row - 1]))):
                classes[row - 1][i] = name
            row, col = row + 1, 0

    tokens = list(tokenize.generate_tokens(io.StringIO(source + "\n").readline))
    for index, tok in enumerate(tokens):
        if tok.type == tokenize.COMMENT:
            paint(tok.start, tok.end, "com")
        elif tok.type in STRING_TYPES:
            paint(tok.start, tok.end, "str")
        elif tok.type == tokenize.NUMBER:
            paint(tok.start, tok.end, "num")
        elif tok.type == tokenize.NAME:
            if keyword.iskeyword(tok.string):
                paint(tok.start, tok.end, "kw")
            elif index + 1 < len(tokens) and tokens[index + 1].string == "(":
                paint(tok.start, tok.end, "fn")
    for line, start, end, role in result.marks:
        if role not in ("subject", "value"):
            paint((line, start), (line, end), "sx-" + role)

    out = []
    for text, row in zip(lines, classes):
        piece, begin = "", 0
        for i in range(1, len(text) + 1):
            if i == len(text) or row[i] != row[begin]:
                chunk = html.escape(text[begin:i], quote=False)
                piece += f'<span class="{row[begin]}">{chunk}</span>' if row[begin] else chunk
                begin = i
        out.append(piece)
    return "\n".join(out)


def build() -> str:
    with open(SOURCE, encoding="utf-8") as handle:
        page = handle.read()

    def render(match):
        kind, code = match.group(1), match.group(2)
        try:
            body = highlight(code) if kind == "seems" else html.escape(code, quote=False)
        except SyntaxError as err:
            raise SystemExit(f"guide sample is not valid Seems (line {err.lineno}): {err.msg}\n---\n{code}\n---")
        return f'<pre class="code {kind}"><code>{body}</code></pre>'

    return BLOCK.sub(render, page)


def main(argv):
    page = build()
    if "--check" in argv:
        with open(TARGET, encoding="utf-8") as handle:
            if handle.read() != page:
                print("docs/index.html is out of date. Run tools/build_guide.py and save the output.")
                return 1
        print("docs/index.html is up to date")
        return 0
    sys.stdout.write(page)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
