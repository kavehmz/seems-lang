"""Seems -> Python translator.

Seems is a superset of Python. This module rewrites the few Seems constructs into
plain Python calls on the runtime (named ``__seems__`` in the generated code) and
leaves every other character untouched.

Two promises:

1. A valid Python file comes out byte-for-byte identical.
2. Every source line keeps its line number, so tracebacks and debuggers point at
   the line the programmer wrote.

The translator never calls a model. It reads tokens with Python's own tokenizer
and applies fixed rules.
"""
from __future__ import annotations

import ast
import io
import keyword
import re
import token as T
import tokenize
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# The vocabulary of the language
# --------------------------------------------------------------------------- #

# Describing verbs: <expression> VERB <English phrase>
# The subject becomes the state as it is, so every judgment about the same value
# shares one request. {p} is the English phrase.
DESCRIBING = {
    "seems": "Does this seem {p}?",
    "sounds": "Does this sound {p}?",
    "looks": "Does this look {p}?",
    "mentions": "Does this mention {p}?",
    "discusses": "Does this discuss {p}?",
    "describes": "Does this describe {p}?",
    "expresses": "Does this express {p}?",
    "suggests": "Does this suggest {p}?",
    "asks": "Does this ask {p}?",
    "requests": "Does this request {p}?",
}

# Relating verbs: <expression> VERB <expression>. Both sides are values. The state
# names both; {s} and {o} are their paths in backticks.
RELATING = {
    "contradicts": "Does {s} contradict {o}?",
    "supports": "Does {s} support {o}?",
    "answers": "Does {s} answer {o}?",
    "means": "Does {s} mean the same as {o}?",
    "matches": "Does {s} match {o}?",
}

VERBS = {**DESCRIBING, **RELATING}
DECLARATIONS = ("kind", "scale", "judgment")

_PHRASE_STOP_WORDS = ("and", "or", "if", "else")
_PHRASE_STOP_OPS = (":", ",", ";")
_OPEN = "([{"
_CLOSE = ")]}"
_SOFT_STATEMENT_WORDS = ("match", "case", "type")
_NOT_LAMBDA_SAFE = (":=", "await", "yield")

_QUICK_CHECK = re.compile(
    r"\b(?:" + "|".join([*VERBS, *DECLARATIONS, "unsure"]) + r")\b"
)

STANDALONE_HEADER = "import seems.runtime as __seems__; from seems.runtime import Unsure\n"


class SeemsSyntaxError(SyntaxError):
    """A mistake in Seems syntax. Behaves like Python's own SyntaxError."""

    def __init__(self, message, filename, lineno, col, text, hint=None):
        full = f"{message}. {hint}" if hint else message
        super().__init__(full, (filename, lineno, (col or 0) + 1, text))
        self.hint = hint
        self.short = message


@dataclass
class Tok:
    type: int
    string: str
    start: int  # offset into the source
    end: int
    line: int  # 1-based physical line of the first character
    col: int


@dataclass
class VerbUse:
    verb: str
    s: int  # index of the first subject token
    v: int  # index of the verb token
    e: int  # index after the last phrase token


@dataclass
class Line:
    toks: list
    depth: list = field(default_factory=list)  # bracket depth before each token
    verbs: list = field(default_factory=list)
    role: str = ""  # "if" | "elif" | "else" | "unsure" | "while" | "decl" | "entry"
    chain: "Chain | None" = None
    block: "Block | None" = None


@dataclass
class Chain:
    lineno: int
    has_unsure: bool = False
    lines: list = field(default_factory=list)
    ahead: list = field(default_factory=list)  # judgments of later branches, asked early

    @property
    def var(self):
        return f"_seems_u{self.lineno}"

    @property
    def managed(self):
        """True when the chain runs through a runtime Chain object."""
        return self.has_unsure or bool(self.ahead)


@dataclass
class Block:
    what: str
    name: str
    entries: list = field(default_factory=list)


@dataclass
class Translation:
    python: str
    changed: list  # 1-based numbers of the lines that were rewritten
    declared: list  # names declared with kind / scale / judgment
    marks: list = field(default_factory=list)  # (line, from_col, to_col, role) for editors


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def translate(source: str, filename: str = "<seems>", standalone: bool = False) -> Translation:
    """Translate Seems source into Python source."""
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    if not _QUICK_CHECK.search(source):
        python = source
        changed, declared = [], []
    else:
        worker = _Translator(source, filename)
        python = worker.run()
        changed, declared = worker.changed_lines(python), sorted(worker.declared)
    if standalone:
        python = _add_standalone_header(python)
    return Translation(python, changed, declared, worker.marks if changed or declared else [])


def compile_seems(source: str, filename: str = "<seems>"):
    """Translate and compile. Syntax errors show the line the programmer wrote."""
    result = translate(source, filename)
    try:
        return compile(result.python, filename, "exec", dont_inherit=True)
    except SyntaxError as err:
        lines = source.splitlines()
        if err.lineno and 0 < err.lineno <= len(lines):
            original = lines[err.lineno - 1]
            if err.text is not None and err.text.rstrip("\n") != original:
                err.text = original + "\n"
                err.offset = None
                err.end_offset = None
        raise


def _add_standalone_header(python: str) -> str:
    """Put the runtime import after any docstring and __future__ imports."""
    lines = python.splitlines(keepends=True)
    insert_at = 0
    try:
        tree = ast.parse(python)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in tree.body:
            is_doc = isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) \
                and isinstance(node.value.value, str) and insert_at == 0
            is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
            if is_doc or is_future:
                insert_at = node.end_lineno
            else:
                break
    lines.insert(insert_at, STANDALONE_HEADER)
    return "".join(lines)


# --------------------------------------------------------------------------- #
# The translator
# --------------------------------------------------------------------------- #


class _Translator:
    def __init__(self, source: str, filename: str):
        self.src = source
        self.filename = filename
        self.src_lines = source.split("\n")
        self.line_starts = [0]
        for text in self.src_lines[:-1]:
            self.line_starts.append(self.line_starts[-1] + len(text) + 1)
        self.declared: set[str] = set()
        self.edits: list[tuple[int, int, str]] = []
        self.marks: list[tuple[int, int, int, str]] = []

    # -- errors ------------------------------------------------------------- #

    def fail(self, message, tok=None, hint=None, lineno=None, col=0):
        if tok is not None:
            lineno, col = tok.line, tok.col
        text = self.src_lines[lineno - 1] if lineno and lineno <= len(self.src_lines) else ""
        raise SeemsSyntaxError(message, self.filename, lineno, col, text, hint)

    # -- step 1: tokens ----------------------------------------------------- #

    def offset(self, row, col):
        return self.line_starts[row - 1] + col

    def tokenize(self):
        raw = []
        try:
            for tok in tokenize.generate_tokens(io.StringIO(self.src).readline):
                raw.append(tok)
        except (tokenize.TokenError, SyntaxError) as err:
            self.tokenize_failed(err)

        fstart = getattr(tokenize, "FSTRING_START", None)
        fend = getattr(tokenize, "FSTRING_END", None)
        tstart = getattr(tokenize, "TSTRING_START", None)
        tend = getattr(tokenize, "TSTRING_END", None)
        starts = {t for t in (fstart, tstart) if t is not None}
        ends = {t for t in (fend, tend) if t is not None}

        lines, current, i = [], [], 0
        skip = {T.NL, T.COMMENT, T.INDENT, T.DEDENT, T.ENDMARKER}
        while i < len(raw):
            tok = raw[i]
            if tok.type in starts:  # fold a whole f-string into one STRING token
                depth, j = 1, i + 1
                while j < len(raw) and depth:
                    if raw[j].type in starts:
                        depth += 1
                    elif raw[j].type in ends:
                        depth -= 1
                    j += 1
                last = raw[j - 1]
                a, b = self.offset(*tok.start), self.offset(*last.end)
                current.append(Tok(T.STRING, self.src[a:b], a, b, tok.start[0], tok.start[1]))
                i = j
                continue
            if tok.type == T.NEWLINE:
                if current:
                    lines.append(Line(current))
                    current = []
            elif tok.type not in skip:
                a, b = self.offset(*tok.start), self.offset(*tok.end)
                current.append(Tok(tok.type, tok.string, a, b, tok.start[0], tok.start[1]))
            i += 1
        if current:
            lines.append(Line(current))
        return lines

    def tokenize_failed(self, err):
        lineno = None
        if isinstance(err, SyntaxError):
            lineno, message = err.lineno, err.msg
        else:
            message = str(err.args[0]) if err.args else str(err)
            if len(err.args) > 1 and isinstance(err.args[1], tuple):
                lineno = err.args[1][0]
        hint = None
        text = self.src_lines[lineno - 1] if lineno and lineno <= len(self.src_lines) else ""
        if "string" in message and any(re.search(rf"\b{v}\b", text) for v in VERBS):
            hint = "If the English part has an apostrophe or other punctuation, put it in quotes"
        raise SeemsSyntaxError(message, self.filename, lineno or 1, 0, text, hint)

    # -- step 2: find the Seems constructs ---------------------------------- #

    def is_expr_end(self, toks, j):
        tok = toks[j]
        if tok.type == T.NAME:
            if j == 0 and tok.string in _SOFT_STATEMENT_WORDS + DECLARATIONS:
                return False
            return not keyword.iskeyword(tok.string) or tok.string in ("True", "False", "None")
        if tok.type in (T.NUMBER, T.STRING):
            return True
        return tok.type == T.OP and tok.string in _CLOSE

    def match_open(self, toks, j):
        depth = 0
        for k in range(j, -1, -1):
            s = toks[k].string
            if toks[k].type == T.OP and s in _CLOSE:
                depth += 1
            elif toks[k].type == T.OP and s in _OPEN:
                depth -= 1
                if depth == 0:
                    return k
        return None

    def match_close(self, toks, j):
        depth = 0
        for k in range(j, len(toks)):
            s = toks[k].string
            if toks[k].type == T.OP and s in _OPEN:
                depth += 1
            elif toks[k].type == T.OP and s in _CLOSE:
                depth -= 1
                if depth == 0:
                    return k
        return None

    def operand_start(self, toks, j):
        """toks[j] ends a primary expression. Return the index where it starts."""
        while True:
            tok = toks[j]
            if tok.type == T.OP and tok.string in _CLOSE:
                k = self.match_open(toks, j)
                if k is None:
                    return j
                if k > 0 and toks[k].string in "([" and self.is_expr_end(toks, k - 1):
                    j = k - 1  # a call or a subscript: keep walking left
                    continue
                return k
            if j >= 2 and toks[j - 1].type == T.OP and toks[j - 1].string == "." \
                    and self.is_expr_end(toks, j - 2):
                j -= 2
                continue
            return j

    def find_verbs(self, line: Line):
        toks, n = line.toks, len(line.toks)
        depth, d = [], 0
        for tok in toks:
            if tok.type == T.OP and tok.string in _CLOSE:
                d = max(0, d - 1)
            depth.append(d)
            if tok.type == T.OP and tok.string in _OPEN:
                d += 1
        line.depth = depth

        i = 1
        while i < n - 1:
            tok = toks[i]
            is_verb = (
                tok.type == T.NAME
                and tok.string in VERBS
                and self.is_expr_end(toks, i - 1)
                and toks[i + 1].line == tok.line
                and toks[i + 1].type in (T.NAME, T.STRING, T.NUMBER)
            )
            if not is_verb:
                i += 1
                continue
            end = self.phrase_end(line, i)
            if end == i + 1:  # nothing after the verb: this is ordinary Python
                i += 1
                continue
            line.verbs.append(VerbUse(tok.string, self.operand_start(toks, i - 1), i, end))
            i = end

    def phrase_end(self, line: Line, v: int):
        toks = line.toks
        base = line.depth[v]
        k, inner = v + 1, 0
        while k < len(toks):
            tok = toks[k]
            if tok.line != toks[v].line:
                break
            if tok.type == T.OP and tok.string in _OPEN:
                inner += 1
            elif tok.type == T.OP and tok.string in _CLOSE:
                if inner == 0:
                    break
                inner -= 1
            elif inner == 0:
                if tok.type == T.OP and tok.string in _PHRASE_STOP_OPS:
                    break
                if tok.type == T.NAME and tok.string in _PHRASE_STOP_WORDS:
                    break
                if tok.type == T.NAME and tok.string == "for" and base > 0 \
                        and self.comprehension_follows(line, k):
                    break
            k += 1
        return k

    def comprehension_follows(self, line: Line, k: int):
        """Is toks[k] the `for` of a comprehension?  True when `for TARGET in` follows
        and TARGET is a real loop target, so "asks for a refund for t in ..." ends at
        the second `for`, not the first."""
        toks, level = line.toks, line.depth[k]
        for j in range(k + 1, len(toks)):
            if line.depth[j] < level:
                return False
            if line.depth[j] == level and toks[j].type == T.NAME and toks[j].string == "in":
                if j == k + 1:
                    return False
                target = self.src[toks[k + 1].start:toks[j - 1].end]
                try:
                    ast.parse(f"for {target} in _: pass")
                except SyntaxError:
                    return False
                return True
        return False

    def find_structure(self, lines):
        """Mark declaration blocks and if-chains."""
        open_chains: dict[int, Chain] = {}
        ended_by_unsure: set[int] = set()
        i = 0
        while i < len(lines):
            line = lines[i]
            toks = line.toks
            first, col = toks[0], toks[0].col
            word = first.string if first.type == T.NAME else ""

            for c in [c for c in open_chains if c > col]:
                del open_chains[c]
            for c in [c for c in ended_by_unsure if c > col]:
                ended_by_unsure.discard(c)

            is_decl = (
                word in DECLARATIONS and len(toks) >= 3
                and toks[1].type == T.NAME and toks[2].type == T.OP and toks[2].string == ":"
            )
            if is_decl:
                open_chains.pop(col, None)
                i = self.read_block(lines, i)
                continue

            if word == "if":
                line.role, line.chain = "if", Chain(first.line)
                line.chain.lines.append(line)
                open_chains[col] = line.chain
                ended_by_unsure.discard(col)
            elif word in ("elif", "else") and col in open_chains:
                if word == "elif" or (len(toks) > 1 and toks[1].string == ":"):
                    line.role, line.chain = word, open_chains[col]
                    line.chain.lines.append(line)
            elif word == "unsure" and len(toks) == 2 and toks[1].string == ":":
                if col not in open_chains:
                    self.fail("'unsure:' has no matching 'if'", first,
                              "Put it after the last branch of an if, at the same indentation")
                line.role, line.chain = "unsure", open_chains.pop(col)
                line.chain.has_unsure = True
                ended_by_unsure.add(col)
            else:
                if word in ("elif", "else") and col in ended_by_unsure:
                    self.fail(f"'{word}' cannot come after 'unsure:'", first,
                              "'unsure:' must be the last branch")
                open_chains.pop(col, None)
                ended_by_unsure.discard(col)
                if word == "while":
                    line.role = "while"
            i += 1

    def read_block(self, lines, i):
        header = lines[i]
        toks = header.toks
        what, name_tok = toks[0].string, toks[1]
        if not name_tok.string.isidentifier() or keyword.iskeyword(name_tok.string):
            self.fail(f"'{name_tok.string}' is not a valid name", name_tok)
        if len(toks) > 4 or (len(toks) == 4 and toks[3].type != T.STRING):
            self.fail(f"A {what} header is: {what} NAME: \"question\"", toks[3],
                      "Put each option on its own indented line below")
        block = Block(what, name_tok.string)
        header.role, header.block = "decl", block
        col = toks[0].col
        j = i + 1
        while j < len(lines) and lines[j].toks[0].col > col:
            entry = lines[j]
            etoks = entry.toks
            key = etoks[0]
            if key.type not in (T.NAME, T.STRING):
                self.fail(f"A {what} entry starts with a name", key)
            if len(etoks) > 1 and not (etoks[1].type == T.OP and etoks[1].string == ":" and len(etoks) > 2):
                self.fail(f"A {what} entry is: name: \"description\"", etoks[1])
            entry.role, entry.block = "entry", block
            block.entries.append(entry)
            j += 1
        names = [e.toks[0].string for e in block.entries]
        if what == "judgment":
            bad = [n for n in names if n not in ("yes", "no")]
            if bad:
                self.fail("A judgment can only describe 'yes' and 'no'", block.entries[names.index(bad[0])].toks[0])
        else:
            minimum = 2
            if len(names) < minimum:
                self.fail(f"A {what} needs at least {minimum} entries", toks[0],
                          "Indent one entry per line below the header")
            if what == "scale" and len(names) > 10:
                self.fail("A scale can have at most 10 levels", toks[0])
            if what == "kind" and len(names) > 255:
                self.fail("A kind can have at most 255 entries", toks[0])
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            self.fail(f"'{sorted(dupes)[0]}' appears twice in {what} {block.name}", toks[0])
        self.declared.add(block.name)
        return j

    # -- step 3: write the Python ------------------------------------------- #

    def text(self, a: Tok, b: Tok):
        return self.src[a.start:b.end]

    def emit(self, line: Line, lo: int, hi: int) -> str:
        """Python text for tokens lo..hi-1 with every judgment verb rewritten."""
        toks = line.toks
        if lo >= hi:
            return ""
        inside = [u for u in line.verbs if lo <= u.s and u.e <= hi]
        outer = [u for u in inside
                 if not any(o is not u and o.s <= u.s and u.e <= o.v for o in inside)]
        out, pos = [], toks[lo].start
        for use in sorted(outer, key=lambda u: u.s):
            out.append(self.src[pos:toks[use.s].start])
            out.append(self.verb_call(line, use))
            pos = toks[use.e - 1].end
        out.append(self.src[pos:toks[hi - 1].end])
        return "".join(out)

    def verb_call(self, line: Line, use: VerbUse) -> str:
        toks = line.toks
        subject = self.emit(line, use.s, use.v)
        subject_src = " ".join(self.text(toks[use.s], toks[use.v - 1]).split())
        phrase_toks = toks[use.v + 1:use.e]
        phrase = self.text(phrase_toks[0], phrase_toks[-1])

        if use.verb in RELATING:
            try:
                ast.parse(phrase.strip(), mode="eval")
            except SyntaxError:
                self.fail(f"'{use.verb}' compares two values", phrase_toks[0],
                          f"Write a variable after '{use.verb}', or put English text in quotes")
            other_src = " ".join(phrase.split())
            return (f"__seems__.relate({use.verb!r}, ({subject}), {subject_src!r}, "
                    f"({phrase}), {other_src!r})")

        if len(phrase_toks) == 1 and phrase_toks[0].type == T.STRING:
            return f"__seems__.noul({use.verb!r}, ({subject}), {subject_src!r}, {phrase})"
        english = " ".join(phrase.split())
        if len(phrase_toks) == 1 and phrase_toks[0].type == T.NAME and phrase.isidentifier() \
                and not keyword.iskeyword(phrase):
            # One word may name a declared judgment. The runtime checks; if it is
            # not one, the word is plain English.
            return (f"__seems__.noul({use.verb!r}, ({subject}), {subject_src!r}, "
                    f"{english!r}, lambda: {phrase})")
        return f"__seems__.noul({use.verb!r}, ({subject}), {subject_src!r}, {english!r})"

    # conditions ---------------------------------------------------------- #

    def header_colon(self, line: Line):
        toks, lambdas = line.toks, 0
        for k in range(1, len(toks)):
            if line.depth[k] != 0:
                continue
            tok = toks[k]
            if tok.type == T.NAME and tok.string == "lambda":
                lambdas += 1
            elif tok.type == T.OP and tok.string == ":":
                if lambdas:
                    lambdas -= 1
                else:
                    return k
        return None

    def words_at_depth0(self, line: Line, lo: int, hi: int, words):
        """Keyword positions at the bracket depth of `lo`, ignoring English phrases."""
        base = line.depth[lo]
        return [k for k in range(lo, hi)
                if line.depth[k] == base and line.toks[k].type == T.NAME and line.toks[k].string in words
                and not any(u.v < k < u.e for u in line.verbs)]

    def uses_judgment(self, line: Line, lo: int, hi: int):
        if any(lo <= u.v < hi for u in line.verbs):
            return True
        toks = line.toks
        return any(toks[k].type == T.NAME and toks[k].string in self.declared for k in range(lo, hi))

    def is_pure(self, line: Line, lo: int, hi: int):
        """True when evaluating the tokens early cannot have side effects we can see."""
        toks = line.toks
        in_phrase = set()
        for use in line.verbs:
            if use.verb in DESCRIBING:
                in_phrase.update(range(use.v + 1, use.e))
        for k in range(lo, hi):
            if k in in_phrase:
                continue
            tok = toks[k]
            if tok.string in _NOT_LAMBDA_SAFE:
                return False
            if tok.type == T.OP and tok.string == "(" and k > lo and self.is_expr_end(toks, k - 1):
                callee = toks[k - 1]
                simple = callee.type == T.NAME and (k - 2 < lo or toks[k - 2].string != ".")
                if not (simple and callee.string in self.declared):
                    return False
        return True

    def condition(self, line: Line, lo: int, hi: int, force: bool) -> str:
        toks = line.toks
        one_line = toks[lo].line == toks[hi - 1].line
        lambda_safe = not any(toks[k].string in _NOT_LAMBDA_SAFE for k in range(lo, hi))
        wanted = force or self.uses_judgment(line, lo, hi)
        if one_line and lambda_safe and wanted and self.words_at_depth0(line, lo, hi, ("and", "or")):
            text, _ = self.bool_or(line, lo, hi)
            return text
        return self.emit(line, lo, hi)

    def is_leaf(self, line: Line, lo: int, hi: int):
        return bool(self.words_at_depth0(line, lo, hi, ("if", "else", "lambda", "for")))

    def split(self, line: Line, lo: int, hi: int, word: str):
        cuts = self.words_at_depth0(line, lo, hi, (word,))
        # a phrase may contain the word only in quotes, so every cut is an operator
        cuts = [k for k in cuts if not any(u.v < k < u.e for u in line.verbs)]
        parts, start = [], lo
        for k in cuts:
            parts.append((start, k))
            start = k + 1
        parts.append((start, hi))
        return [(a, b) for a, b in parts if a < b]

    def bool_or(self, line, lo, hi):
        if self.is_leaf(line, lo, hi):
            return self.emit(line, lo, hi), self.is_pure(line, lo, hi)
        parts = self.split(line, lo, hi, "or")
        if len(parts) == 1:
            return self.bool_and(line, lo, hi)
        return self.combine("any_", [self.bool_and(line, a, b) for a, b in parts])

    def bool_and(self, line, lo, hi):
        parts = self.split(line, lo, hi, "and")
        if len(parts) == 1:
            return self.bool_not(line, lo, hi)
        return self.combine("all_", [self.bool_not(line, a, b) for a, b in parts])

    def bool_not(self, line, lo, hi):
        toks = line.toks
        if toks[lo].type == T.NAME and toks[lo].string == "not" and hi - lo > 1:
            text, pure = self.bool_not(line, lo + 1, hi)
            return f"__seems__.not_({text})", pure
        if toks[lo].type == T.OP and toks[lo].string == "(" and self.match_close(toks, lo) == hi - 1 \
                and hi - lo > 2:
            text, pure = self.bool_or(line, lo + 1, hi - 1)
            return f"({text})", pure
        return self.emit(line, lo, hi), self.is_pure(line, lo, hi)

    @staticmethod
    def combine(name, items):
        thunks = ", ".join(f"lambda: ({text})" for text, _ in items)
        spec = ", ".join("1" if pure else "0" for _, pure in items)
        return f"__seems__.{name}({thunks}, spec=({spec}))", all(pure for _, pure in items)

    # lines ------------------------------------------------------------------ #

    def plan_ahead(self, chain: Chain):
        """Judgments in the `elif` conditions can be asked together with the `if`.

        This is the speculative fan-out pattern from the TypeSafe docs: one round
        trip for the whole if/elif statement. Only judgments whose operands cannot
        have side effects are asked ahead. The answers are used if that branch is
        reached and ignored otherwise.
        """
        first_has_judgment = False
        for line in chain.lines:
            if line.role not in ("if", "elif"):
                continue
            colon = self.header_colon(line)
            if colon is None or colon < 2:
                continue
            if line.role == "if":
                first_has_judgment = self.uses_judgment(line, 1, colon)
                continue
            toks = line.toks
            for use in line.verbs:
                if not (1 <= use.s and use.e <= colon) or not self.is_pure(line, use.s, use.e):
                    continue
                if any(o is not use and o.s <= use.s and use.e <= o.v for o in line.verbs):
                    continue
                chain.ahead.append(self.verb_call(line, use))
            for k in range(1, colon - 1):
                is_call = (toks[k].type == T.NAME and toks[k].string in self.declared
                           and toks[k + 1].string == "(" and toks[k - 1].string != ".")
                if not is_call or any(u.v < k < u.e for u in line.verbs):
                    continue
                close = self.match_close(toks, k + 1)
                if close is not None and close < colon and self.is_pure(line, k, close + 1):
                    chain.ahead.append(self.emit(line, k, close + 1))
        chain.ahead = [text for text in dict.fromkeys(chain.ahead) if "\n" not in text]
        if not first_has_judgment and not chain.has_unsure and len(chain.ahead) < 2:
            chain.ahead = []  # nothing to gain: the elif asks its single judgment itself

    def rewrite_header(self, line: Line):
        toks = line.toks
        colon = self.header_colon(line)
        chain = line.chain if line.chain and line.chain.managed else None
        if colon is None or colon < 2:
            return self.rewrite_plain(line)
        lo, hi = 1, colon
        cond = self.condition(line, lo, hi, force=chain is not None)
        if chain is not None:
            lambda_safe = not any(toks[k].string in _NOT_LAMBDA_SAFE for k in range(lo, hi))
            call = f"test(lambda: ({cond}))" if lambda_safe else f"check({cond})"
            if line.role == "if":
                options = []
                if chain.ahead:
                    thunks = ", ".join(f"lambda: {text}" for text in chain.ahead)
                    options.append(f"ahead=({thunks},)")
                if not chain.has_unsure:
                    options.append("strict=True")
                owner = f"({chain.var} := __seems__.Chain({', '.join(options)}))"
            else:
                owner = chain.var
            cond = f"{owner}.{call}"
        rest = self.src[toks[colon - 1].end:toks[colon].end]
        if colon + 1 < len(toks):
            rest += self.src[toks[colon].end:toks[colon + 1].start] + self.emit(line, colon + 1, len(toks))
        new = self.src[toks[0].start:toks[1].start] + cond + rest
        self.replace(toks[0], toks[-1], new)

    def rewrite_plain(self, line: Line):
        if line.verbs:
            self.replace(line.toks[0], line.toks[-1], self.emit(line, 0, len(line.toks)))

    def rewrite_decl(self, line: Line):
        toks, block = line.toks, line.block
        question = toks[3].string if len(toks) == 4 else "None"
        self.replace(toks[0], toks[-1],
                     f"{block.name} = __seems__.{block.what}({block.name!r}, {question}, [")
        if not block.entries:
            self.replace(toks[0], toks[-1],
                         f"{block.name} = __seems__.{block.what}({block.name!r}, {question}, [])")
        for n, entry in enumerate(block.entries):
            etoks = entry.toks
            key = etoks[0].string if etoks[0].type == T.STRING else repr(etoks[0].string)
            desc = self.text(etoks[2], etoks[-1]) if len(etoks) > 2 else "None"
            closing = "])" if n == len(block.entries) - 1 else ","
            self.replace(etoks[0], etoks[-1], f"({key}, {desc}){closing}")

    def replace(self, first: Tok, last: Tok, new: str):
        if self.src[first.start:last.end] != new:
            self.edits.append((first.start, last.end, new))

    # -- run ------------------------------------------------------------------ #

    def run(self) -> str:
        lines = self.tokenize()
        for line in lines:
            self.find_verbs(line)
        self.find_structure(lines)
        for line in lines:
            if line.role == "if":
                self.plan_ahead(line.chain)
        for line in lines:
            if line.role == "entry":
                continue
            if line.role == "decl":
                self.rewrite_decl(line)
            elif line.role in ("if", "elif", "while"):
                self.rewrite_header(line)
            elif line.role == "else" and line.chain.has_unsure:
                self.replace(line.toks[0], line.toks[0], f"elif {line.chain.var}.sure")
                self.rewrite_tail(line)
            elif line.role == "unsure":
                self.replace(line.toks[0], line.toks[0], f"elif {line.chain.var}.unsure")
            else:
                self.rewrite_plain(line)

        self.collect_marks(lines)
        out, pos = [], 0
        for start, end, new in sorted(self.edits):
            out.append(self.src[pos:start])
            out.append(new)
            pos = end
        out.append(self.src[pos:])
        python = "".join(out)
        if python.count("\n") != self.src.count("\n"):  # promise 2
            raise AssertionError("translator changed the number of lines")
        return python

    def mark(self, first: Tok, last: Tok, role: str):
        if first.line == last.line and "\n" not in last.string:
            self.marks.append((first.line, first.col, last.col + len(last.string), role))

    def collect_marks(self, lines):
        """Where the Seems words are, so an editor can colour them like the parser sees them."""
        for line in lines:
            toks = line.toks
            if line.role == "decl":
                self.mark(toks[0], toks[0], "keyword")
                self.mark(toks[1], toks[1], "name")
            elif line.role == "entry":
                self.mark(toks[0], toks[0], "entry")
                continue
            elif line.role == "unsure":
                self.mark(toks[0], toks[0], "keyword")
            for use in line.verbs:
                self.mark(toks[use.v], toks[use.v], "verb")
                self.mark(toks[use.v + 1], toks[use.e - 1], "value" if use.verb in RELATING else "phrase")
                self.mark(toks[use.s], toks[use.v - 1], "subject")

    def rewrite_tail(self, line: Line):
        """`else: <statement>` on one line: rewrite verbs in the statement."""
        toks = line.toks
        if len(toks) > 2 and any(u.v >= 2 for u in line.verbs):
            self.replace(toks[2], toks[-1], self.emit(line, 2, len(toks)))

    def changed_lines(self, python: str):
        new_lines = python.split("\n")
        return [n + 1 for n, (a, b) in enumerate(zip(self.src_lines, new_lines)) if a != b]
