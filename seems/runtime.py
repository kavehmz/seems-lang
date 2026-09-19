"""Seems runtime.

The generated Python calls into this module under the name ``__seems__``.

Main ideas:

* A judgment is **lazy**. Creating one sends nothing. The first time a program
  needs any answer, everything still waiting goes to Jev together: questions
  with the same state share one request, other requests run side by side.
* A judgment is **three-valued**: yes, no or unsure. ``bool()`` of an unsure
  judgment raises ``Unsure``, so a program can never act on a guess by accident.
* Every answer is **cached** by (model, state, question), so a second run gives
  the same result and costs nothing.
"""
from __future__ import annotations

import contextvars
import dataclasses
import hashlib
import itertools
import json
import linecache
import operator
import os
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .client import JevClient, JevError
from .translator import DESCRIBING, RELATING

YES, NO, UNSURE = "yes", "no", "unsure"
PRICE_PER_MTOK_USD = 0.042  # docs.typesafe.ai/models, input tokens; output is free

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


class _Settings:
    def __init__(self):
        self.sure = 0.75
        self.model = os.environ.get("TYPESAFE_MODEL") or "jev-latest"
        self.workers = 8
        self.max_questions = 40
        self.client = None
        self.cache = os.environ.get("SEEMS_NO_CACHE", "") not in ("1", "true", "yes")


settings = _Settings()
_sure_override = contextvars.ContextVar("seems_sure", default=None)
_collector = contextvars.ContextVar("seems_trace", default=None)


def configure(*, sure=None, model=None, client=None, cache=None, workers=None):
    """Change the defaults for the whole program."""
    if sure is not None:
        settings.sure = _check_level(sure)
    if model is not None:
        settings.model = model
    if client is not None:
        settings.client = client
    if cache is not None:
        settings.cache = bool(cache)
    if workers is not None:
        settings.workers = max(1, int(workers))


def _check_level(level):
    level = float(level)
    if not 0.5 < level <= 1.0:
        raise ValueError("certainty must be above 0.5 and at most 1.0")
    return level


def sure_level() -> float:
    """How sure a judgment must be to count as yes (or, mirrored, as no)."""
    override = _sure_override.get()
    return settings.sure if override is None else override


class certainty:
    """``with seems.certainty(0.9):`` raises the bar inside the block."""

    def __init__(self, level):
        self.level = _check_level(level)

    def __enter__(self):
        self._token = _sure_override.set(self.level)
        return self

    def __exit__(self, *exc):
        _sure_override.reset(self._token)
        return False


# --------------------------------------------------------------------------- #
# Three-valued truth
# --------------------------------------------------------------------------- #


class Unsure(Exception):
    """A judgment was needed as yes or no, but Jev was not sure enough."""

    def __init__(self, judgment):
        self.judgment = judgment
        super().__init__(judgment.explain())


Unsure.__module__ = "seems"  # tracebacks say seems.Unsure, the name programs import


class Truth:
    """Something that is yes, no or unsure."""

    def verdict(self) -> str:
        raise NotImplementedError

    def explain(self) -> str:
        return f"{self!r} is {self.verdict()}"

    @property
    def yes(self):
        return self.verdict() == YES

    @property
    def no(self):
        return self.verdict() == NO

    @property
    def unsure(self):
        return self.verdict() == UNSURE

    @property
    def sure(self):
        return self.verdict() != UNSURE

    def __bool__(self):
        verdict = self.verdict()
        if verdict == UNSURE:
            raise Unsure(self)
        return verdict == YES

    # `&`, `|` and `~` combine judgments without forcing an answer.
    def __and__(self, other):
        return _Combo(False, [self, other])

    __rand__ = __and__

    def __or__(self, other):
        return _Combo(True, [self, other])

    __ror__ = __or__

    def __invert__(self):
        return _Not(self)


class _Fixed(Truth):
    def __init__(self, verdict, why=""):
        self._verdict, self._why = verdict, why

    def verdict(self):
        return self._verdict

    def explain(self):
        return self._why or f"the condition is {self._verdict}"

    def __repr__(self):
        return f"<{self._verdict}>"


def _verdict_of(value) -> str:
    if isinstance(value, Truth):
        return value.verdict()
    return YES if value else NO


def _kleene(is_any: bool, verdicts) -> str:
    decided, neutral = (YES, NO) if is_any else (NO, YES)
    if decided in verdicts:
        return decided
    return UNSURE if UNSURE in verdicts else neutral


class _Combo(Truth):
    def __init__(self, is_any, parts):
        self._is_any, self._parts = is_any, parts

    def verdict(self):
        return _kleene(self._is_any, [_verdict_of(p) for p in self._parts])

    def explain(self):
        unsure = [p.explain() for p in self._parts if isinstance(p, Truth) and p.unsure]
        return "; ".join(unsure) or super().explain()

    def __repr__(self):
        return (" | " if self._is_any else " & ").join(repr(p) for p in self._parts)


class _Not(Truth):
    def __init__(self, inner):
        self._inner = inner

    def verdict(self):
        return {YES: NO, NO: YES, UNSURE: UNSURE}[self._inner.verdict()]

    def explain(self):
        return self._inner.explain()

    def __repr__(self):
        return f"~{self._inner!r}"


class _ProbTruth(Truth):
    """Truth backed by one probability. The certainty level turns it into a verdict."""

    @property
    def p(self) -> float:
        raise NotImplementedError

    def verdict(self):
        p, level = self.p, sure_level()
        if p >= level:
            return YES
        if p <= 1.0 - level:
            return NO
        return UNSURE

    def __float__(self):
        return float(self.p)


class Derived(_ProbTruth):
    """A yes/no reading of a Pick or a Rating, for example ``rating >= level``."""

    def __init__(self, parent, fn, text):
        self._parent, self._fn, self._text = parent, fn, text

    @property
    def p(self):
        return self._fn(self._parent)

    def explain(self):
        return (f"line {self._parent._question.line}: {self._text} has probability {self.p:.2f}. "
                f"That is not sure enough (needs at least {sure_level():.2f} or at most "
                f"{1 - sure_level():.2f}). Add an 'unsure:' branch or catch Unsure")

    def __repr__(self):
        return f"<{self._text}>"


# --------------------------------------------------------------------------- #
# Questions and lazy answers
# --------------------------------------------------------------------------- #


class _Question:
    def __init__(self, kind, instructions, criteria, state, source):
        self.kind, self.instructions, self.criteria = kind, instructions, criteria
        self.state = state
        self.file, self.line = _where()
        if source is None:  # kind(...), scale(...): show the line the programmer wrote
            written = linecache.getline(self.file, self.line).strip() if self.file else ""
            source = written if len(written) <= 90 else written[:87] + "..."
        self.source = source or instructions
        self.payload = {"type": kind, "instructions": instructions}
        if criteria is not None:
            self.payload["criteria"] = criteria
        self.state_key = json.dumps(state, sort_keys=True, ensure_ascii=False)
        self.question_key = json.dumps(self.payload, sort_keys=True, ensure_ascii=False)


def _where():
    """File and line of the program code that asked for the judgment."""
    frame = sys._getframe(1)
    while frame is not None:
        filename = frame.f_code.co_filename
        if not filename.startswith(_PKG_DIR):
            return filename, frame.f_lineno
        frame = frame.f_back
    return "", 0


class Lazy:
    """An answer that is fetched the first time the program needs it."""

    def __init__(self, question: _Question):
        self._question = question
        self._answer = None
        self._error = None
        self._done = False
        self._cached = False
        self._request = None
        self._ahead = _engine.asking_ahead()
        self._event_id = None
        _engine.submit(self)

    def _get(self):
        if not self._done:
            _engine.flush()
        if not self._done:  # created on another thread, still in that thread's queue
            _engine.resolve([self])
        if self._error is not None:
            raise self._error
        return self._answer

    @property
    def ready(self):
        return self._done


class Judgment(_ProbTruth, Lazy):
    """One yes/no judgment (a Jev Noul)."""

    def __init__(self, question):
        Lazy.__init__(self, question)

    @property
    def p(self):
        return float(self._get()["noul"])

    def explain(self):
        q = self._question
        return (f"line {q.line}: \"{q.source}\" came back {self.p:.2f}. That is not sure enough "
                f"(needs at least {sure_level():.2f} or at most {1 - sure_level():.2f}). "
                f"Add an 'unsure:' branch or catch Unsure")

    def __repr__(self):
        if not self._done:
            return f"<judgment {self._question.source!r} waiting>"
        return f"<judgment {self._question.source!r} {self.p:.2f} {self.verdict()}>"


# --------------------------------------------------------------------------- #
# kind  ->  Jev Choice
# --------------------------------------------------------------------------- #


class Member:
    """One option of a kind."""

    def __init__(self, kind, name, description, index):
        self.kind, self.name, self.description, self.index = kind, name, description, index

    def __eq__(self, other):
        if isinstance(other, Pick):
            return other.__eq__(self)
        if isinstance(other, Member):
            return other is self
        if isinstance(other, str):
            return other == self.name
        return NotImplemented

    def __hash__(self):
        return hash((id(self.kind), self.name))

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"{self.kind._name}.{self.name}"


class _Declared:
    """Shared by kind and scale: named entries reachable as attributes."""

    _what = "kind"

    def __init__(self, name, question, entries, entry_type):
        self._name = name
        self._question_text = question
        self._entries = [entry_type(self, str(n), d, i) for i, (n, d) in enumerate(entries)]
        self._by_name = {e.name: e for e in self._entries}

    def __getattr__(self, name):
        try:
            return self.__dict__["_by_name"][name]
        except KeyError:
            raise AttributeError(f"{self._what} {self.__dict__.get('_name')} has no entry '{name}'") from None

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._entries[key]
        return self._by_name[key]

    def __iter__(self):
        return iter(self._entries)

    def __len__(self):
        return len(self._entries)

    def __contains__(self, item):
        return item in self._by_name or item in self._entries

    def __repr__(self):
        return f"<{self._what} {self._name}: {', '.join(self._by_name)}>"


class Kind(_Declared):
    _what = "kind"

    def __init__(self, name, question, entries):
        super().__init__(name, question or f"Which {name} fits best?", entries, Member)

    def __call__(self, *args, **named):
        criteria = {m.name: m.description for m in self._entries}
        question = _Question("choice", self._question_text, criteria, _state_from(args, named), None)
        return Pick(self, question)


class Pick(Lazy):
    """The answer of a kind: the chosen member and how likely each member is."""

    def __init__(self, kind, question):
        self._kind = kind
        super().__init__(question)

    @property
    def top(self) -> Member:
        return self._kind[self._get()["choice"]]

    @property
    def name(self) -> str:
        return self.top.name

    @property
    def confidence(self) -> float:
        return float(self._get().get("confidence", 0.0))

    @property
    def probabilities(self) -> dict:
        probs = self._get()["probabilities"]
        return {m.name: float(probs.get(m.name, 0.0)) for m in self._kind}

    def p(self, option) -> float:
        return self.probabilities[self._member(option).name]

    @property
    def sure(self) -> bool:
        return self.p(self.top) >= sure_level()

    def _member(self, option) -> Member:
        if isinstance(option, Member) and option.kind is self._kind:
            return option
        if isinstance(option, str) and option in self._kind._by_name:
            return self._kind[option]
        raise ValueError(f"{option!r} is not an entry of kind {self._kind._name}")

    def is_one_of(self, *options) -> Truth:
        members = [self._member(o) for o in options]
        text = f"{self._kind._name} is one of {', '.join(m.name for m in members)}"
        return Derived(self, lambda pick: sum(pick.probabilities[m.name] for m in members), text)

    def __eq__(self, other):
        if not isinstance(other, (Member, str)):
            return NotImplemented
        member = self._member(other)
        return Derived(self, lambda pick: pick.probabilities[member.name],
                       f"{self._kind._name} is {member.name}")

    def __ne__(self, other):
        if not isinstance(other, (Member, str)):
            return NotImplemented
        member = self._member(other)
        return Derived(self, lambda pick: 1.0 - pick.probabilities[member.name],
                       f"{self._kind._name} is not {member.name}")

    __hash__ = object.__hash__

    def __str__(self):
        return self.name

    def __format__(self, spec):
        return format(self.name, spec)

    def __repr__(self):
        if not self._done:
            return f"<{self._kind._name} waiting>"
        return f"<{self._kind._name} {self.name} {self.p(self.top):.2f}>"


# --------------------------------------------------------------------------- #
# scale  ->  Jev Score
# --------------------------------------------------------------------------- #


class Level(Member):
    """One step of a scale. Levels are ordered."""

    def __lt__(self, other):
        return self.index < other.index if isinstance(other, Level) else NotImplemented

    def __le__(self, other):
        return self.index <= other.index if isinstance(other, Level) else NotImplemented

    def __eq__(self, other):
        if isinstance(other, Rating):
            return other.__eq__(self)
        return super().__eq__(other)

    __hash__ = Member.__hash__


class Scale(_Declared):
    _what = "scale"

    def __init__(self, name, question, entries):
        super().__init__(name, question or f"How {name} is this?", entries, Level)

    def __call__(self, *args, **named):
        # Jev sees the level descriptions and nothing else, so a level without a
        # description falls back to its name.
        criteria = [level.description or level.name for level in self._entries]
        question = _Question("score", self._question_text, criteria, _state_from(args, named), None)
        return Rating(self, question)


_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
        "==": operator.eq, "!=": operator.ne}


class Rating(Lazy):
    """The answer of a scale: a position on its levels."""

    def __init__(self, scale, question):
        self._scale = scale
        super().__init__(question)

    @property
    def score(self) -> float:
        return float(self._get()["score"])

    @property
    def normalized(self) -> float:
        return self.score / max(1, len(self._scale) - 1)

    @property
    def level(self) -> Level:
        return self._scale[min(len(self._scale) - 1, max(0, round(self.score)))]

    @property
    def confidence(self) -> float:
        return float(self._get().get("confidence", 0.0))

    @property
    def probabilities(self) -> dict:
        probs = self._get()["probabilities"]
        return {lv.name: float(probs.get(str(lv.index), probs.get(lv.index, 0.0))) for lv in self._scale}

    def _compare(self, symbol, other):
        op = _OPS[symbol]
        if isinstance(other, Rating):
            return op(self.score, other.score)
        if isinstance(other, Level):
            if other.kind is not self._scale:
                raise ValueError(f"{other!r} is not a level of scale {self._scale._name}")
            index, label = other.index, other.name
        elif isinstance(other, str) and other in self._scale._by_name:
            index, label = self._scale[other].index, other
        elif isinstance(other, bool) or not isinstance(other, (int, float)):
            return NotImplemented
        elif float(other).is_integer():
            index, label = int(other), str(int(other))
        else:
            return op(self.score, other)  # a position between two levels: compare the score

        def probability(rating):
            probs = list(rating.probabilities.values())
            return sum(p for i, p in enumerate(probs) if op(i, index))

        return Derived(self, probability, f"{self._scale._name} {symbol} {label}")

    def __lt__(self, other):
        return self._compare("<", other)

    def __le__(self, other):
        return self._compare("<=", other)

    def __gt__(self, other):
        return self._compare(">", other)

    def __ge__(self, other):
        return self._compare(">=", other)

    def __eq__(self, other):
        return self._compare("==", other)

    def __ne__(self, other):
        return self._compare("!=", other)

    __hash__ = object.__hash__

    def __float__(self):
        return self.score

    def __str__(self):
        return self.level.name

    def __format__(self, spec):
        return format(self.level.name, spec)

    def __repr__(self):
        if not self._done:
            return f"<{self._scale._name} waiting>"
        return f"<{self._scale._name} {self.level.name} {self.score:.2f}>"


# --------------------------------------------------------------------------- #
# judgment  ->  a named Jev Noul with criteria
# --------------------------------------------------------------------------- #


class JudgmentDef:
    def __init__(self, name, question, entries):
        if not question:
            raise ValueError(f"judgment {name} needs a question in quotes after the colon")
        self._name, self._question_text = name, question
        found = dict(entries)
        self._criteria = None
        if found.get("yes") or found.get("no"):
            self._criteria = {}
            if found.get("yes"):
                self._criteria["true"] = found["yes"]
            if found.get("no"):
                self._criteria["false"] = found["no"]

    def _make(self, state, source):
        return Judgment(_Question("noul", self._question_text, self._criteria, state, source))

    def __call__(self, *args, **named):
        return self._make(_state_from(args, named), None)

    def __repr__(self):
        return f"<judgment {self._name}: {self._question_text}>"


def ask(question, *args, yes=None, no=None, **named) -> Judgment:
    """A one-off yes/no judgment with your own full question text."""
    criteria = {k: v for k, v in (("true", yes), ("false", no)) if v} or None
    return Judgment(_Question("noul", str(question), criteria, _state_from(args, named), str(question)))


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #

_PATH = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (Pick, Rating)):
        return str(value)
    if isinstance(value, _ProbTruth):
        return round(value.p, 4)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if hasattr(value, "_asdict"):
        return _jsonable(value._asdict())
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    if hasattr(value, "keys") and hasattr(value, "__getitem__"):  # sqlite3.Row and friends
        return {str(k): _jsonable(value[k]) for k in value.keys()}
    return str(value)


def _state_from(args, named):
    """State for kind(...), scale(...), judgment(...) and ask(...)."""
    if named:
        state = {k: _jsonable(v) for k, v in named.items()}
        if len(args) == 1:
            state = {"text": _jsonable(args[0]), **state}
        elif args:
            state = {"items": _jsonable(args), **state}
        return state
    if len(args) == 1:
        return _jsonable(args[0])
    if not args:
        raise TypeError("give the judgment something to look at")
    return _jsonable(args)


def _label(source: str, value, fallback: str):
    """Turn `ticket.text` into a path Jev can read. Other expressions get a plain name."""
    if _PATH.match(source):
        parts = source.split(".")
        if parts[0] in ("self", "cls") and len(parts) > 1:
            parts = parts[1:]
        return parts
    return [fallback or ("text" if isinstance(value, str) else "value")]


def _nest(parts, value):
    for part in reversed(parts):
        value = {part: value}
    return value


def _merge(a: dict, b: dict):
    out = dict(a)
    for key, value in b.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _tick(parts):
    return "`" + ".".join(parts) + "`"


def _clean_phrase(phrase) -> str:
    return " ".join(str(phrase).split()).rstrip("?.! ")


def noul(verb, subject, subject_src, phrase, ref=None) -> Judgment:
    """`subject VERB english phrase`

    The state is the subject itself, the same state that kind(...), scale(...) and
    judgment(...) build. So every question about one value travels in one request.
    """
    state = _jsonable(subject)
    source = f"{subject_src} {verb} {_clean_phrase(phrase)}"
    if ref is not None:
        try:
            named = ref()
        except Exception:
            named = None
        if isinstance(named, JudgmentDef):
            return named._make(state, source)
    instructions = DESCRIBING[verb].format(p=_clean_phrase(phrase))
    return Judgment(_Question("noul", instructions, None, state, source))


def relate(verb, subject, subject_src, other, other_src) -> Judgment:
    """`subject VERB other` where both sides are values."""
    left = _label(subject_src, subject, "")
    right = _label(other_src, other, "other")
    clash = left == right or left[:len(right)] == right or right[:len(left)] == left
    if clash:
        right = ["other"] if left != ["other"] else ["second"]
    state = _merge(_nest(left, _jsonable(subject)), _nest(right, _jsonable(other)))
    instructions = RELATING[verb].format(s=_tick(left), o=_tick(right))
    return Judgment(_Question("noul", instructions, None, state, f"{subject_src} {verb} {other_src}"))


# Names used by the translated declaration blocks.
def kind(name, question, entries):
    return Kind(name, question, entries)


def scale(name, question, entries):
    return Scale(name, question, entries)


def judgment(name, question, entries):
    return JudgmentDef(name, question, entries)


# --------------------------------------------------------------------------- #
# Conditions: `and`, `or`, `not` with three values, asked together
# --------------------------------------------------------------------------- #


def not_(value):
    return _Not(value) if isinstance(value, Truth) else (not value)


def all_(*thunks, spec=None):
    return _combine(False, thunks, spec)


def any_(*thunks, spec=None):
    return _combine(True, thunks, spec)


def _combine(is_any, thunks, spec):
    """Evaluate `a and b and ...` (or `or`) the way Python would, with two extras:

    * a judgment is not forced at once. Judgments that follow each other are
      created first and then asked together.
    * the result follows three-valued logic, so `no and unsure` is no.

    `spec[i]` is 1 when thunk i cannot have side effects. Only those may run
    before the judgments in front of them are known.
    """
    spec = spec or (0,) * len(thunks)
    decided = YES if is_any else NO
    waiting, seen_unsure = [], False

    def settle():
        nonlocal waiting, seen_unsure
        verdicts = [t.verdict() for t in waiting]
        waiting = []
        if UNSURE in verdicts:
            seen_unsure = True
        return decided in verdicts

    for thunk, pure in zip(thunks, spec):
        if not pure and (waiting or seen_unsure):
            if settle():
                return _Fixed(decided)
            if seen_unsure:  # Python would not know whether to run this part
                return _Fixed(UNSURE, "an earlier part of the condition is unsure")
        try:
            value = thunk()
        except Unsure:
            seen_unsure = True
            continue
        except Exception:
            if waiting and settle():
                return _Fixed(decided)
            raise
        if isinstance(value, Truth):
            waiting.append(value)
        elif bool(value) == is_any:
            for truth in waiting:
                _engine.cancel(truth)
            return _Fixed(decided)

    explanations = [t for t in waiting]
    if settle():
        return _Fixed(decided)
    if seen_unsure:
        why = "; ".join(t.explain() for t in explanations if t.unsure)
        return _Fixed(UNSURE, why or "part of the condition is unsure")
    return _Fixed(NO if is_any else YES)


class Chain:
    """One `if / elif / else / unsure` statement while it runs.

    `ahead` holds the judgments of the later `elif` branches. They are created
    here, before the first test, so they travel to Jev in the same round trip as
    the `if` condition. A branch that is never reached simply ignores its answer.

    `strict` is for a statement without an `unsure:` branch: unsure raises Unsure.
    """

    def __init__(self, ahead=(), strict=False):
        self.unsure = False
        self.reason = None
        self._strict = strict
        self._ahead = []
        if ahead:
            _engine.local.ahead = True
            try:
                for thunk in ahead:
                    try:
                        value = thunk()
                    except Exception:  # e.g. a name that only exists in that branch
                        continue
                    value = getattr(value, "_parent", value)
                    if isinstance(value, Lazy):
                        self._ahead.append(value)
            finally:
                _engine.local.ahead = False

    @property
    def sure(self):
        return not self.unsure

    def test(self, thunk):
        if self.unsure:
            return False
        try:
            return self._decide(thunk())
        except Unsure as unsure:
            return self._became_unsure(unsure)

    def check(self, value):
        if self.unsure:
            return False
        try:
            return self._decide(value)
        except Unsure as unsure:
            return self._became_unsure(unsure)

    def _decide(self, value):
        if isinstance(value, Truth):
            verdict = value.verdict()
            if verdict == UNSURE:
                return self._became_unsure(value)
            taken = verdict == YES
        else:
            taken = bool(value)
        if taken:
            self._drop()
        return taken

    def _became_unsure(self, reason):
        self._drop()
        if self._strict:
            raise reason if isinstance(reason, Unsure) else Unsure(reason)
        self.unsure, self.reason = True, reason
        return False

    def _drop(self):
        """The statement is decided: judgments asked ahead but not sent are not needed."""
        for lazy in self._ahead:
            _engine.cancel(lazy)
        self._ahead = []


# --------------------------------------------------------------------------- #
# Engine: queue, cache, requests, trace
# --------------------------------------------------------------------------- #


class _DiskCache:
    def __init__(self, path):
        self.path = path
        self.ok = True
        try:
            with self._open() as db:
                db.execute("create table if not exists answers (key text primary key, answer text)")
        except sqlite3.Error:
            self.ok = False

    def _open(self):
        return sqlite3.connect(self.path, timeout=3)

    def get(self, key):
        if not self.ok:
            return None
        try:
            with self._open() as db:
                row = db.execute("select answer from answers where key = ?", (key,)).fetchone()
            return json.loads(row[0]) if row else None
        except (sqlite3.Error, ValueError):
            return None

    def put(self, key, answer):
        if not self.ok:
            return
        try:
            with self._open() as db:
                db.execute("insert or replace into answers values (?, ?)", (key, json.dumps(answer)))
        except sqlite3.Error:
            pass

    def clear(self):
        try:
            with self._open() as db:
                db.execute("delete from answers")
        except sqlite3.Error:
            pass


class _Engine:
    def __init__(self):
        self.local = threading.local()
        self.lock = threading.Lock()
        self.memory = {}
        self.disk = None
        self.disk_path = None
        self.judgment_ids = itertools.count(1)
        self.request_ids = itertools.count(1)
        self.totals = {"judgments": 0, "cached": 0, "requests": 0, "questions": 0,
                       "input_tokens": 0, "output_tokens": 0, "ms": 0}
        self.trace_file = None

    # queue ----------------------------------------------------------------- #

    def queue(self):
        if not hasattr(self.local, "queue"):
            self.local.queue = []
        return self.local.queue

    def submit(self, lazy):
        self.queue().append(lazy)

    def asking_ahead(self):
        return getattr(self.local, "ahead", False)

    def ahead_answers(self):
        if not hasattr(self.local, "ahead_answers"):
            self.local.ahead_answers = {}
        return self.local.ahead_answers

    def cancel(self, truth):
        if isinstance(truth, Lazy) and not truth._done:
            try:
                self.queue().remove(truth)
            except ValueError:
                pass

    def flush(self):
        batch, self.local.queue = self.queue(), []
        self.resolve(batch)

    # cache ----------------------------------------------------------------- #

    def cache_key(self, question):
        raw = json.dumps([settings.model, question.state_key, question.question_key])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def disk_cache(self):
        path = os.environ.get("SEEMS_CACHE")
        if path != self.disk_path:
            self.disk_path, self.disk = path, (_DiskCache(path) if path else None)
        return self.disk

    def cache_get(self, key):
        if key in self.memory:
            return self.memory[key]
        disk = self.disk_cache()
        found = disk.get(key) if disk else None
        if found is not None:
            self.memory[key] = found
        return found

    def cache_put(self, key, answer):
        self.memory[key] = answer
        disk = self.disk_cache()
        if disk:
            disk.put(key, answer)

    def clear_cache(self):
        self.memory.clear()
        disk = self.disk_cache()
        if disk:
            disk.clear()

    # requests ---------------------------------------------------------------- #

    def resolve(self, batch):
        batch = [lazy for lazy in batch if not lazy._done]
        if not batch:
            return
        by_key, order = {}, []
        for lazy in batch:
            key = self.cache_key(lazy._question)
            if key not in by_key:
                by_key[key] = []
                order.append(key)
            by_key[key].append(lazy)

        jobs = {}  # state_key -> list of cache keys
        used_ahead = []
        memo = self.ahead_answers()
        for key in order:
            if key in memo and not all(lazy._ahead for lazy in by_key[key]):
                answer, event_id = memo.pop(key)  # asked ahead by the if-statement: use it now
                for lazy in by_key[key]:
                    lazy._answer, lazy._done, lazy._event_id = answer, True, event_id
                used_ahead.append(key)
                real = next(lazy for lazy in by_key[key] if not lazy._ahead)._question
                self.emit({"type": "used", "id": event_id, "line": real.line})
                continue
            cached = self.cache_get(key) if settings.cache else None
            if cached is not None:
                for lazy in by_key[key]:
                    lazy._answer, lazy._cached, lazy._done = cached, True, True
            else:
                jobs.setdefault(by_key[key][0]._question.state_key, []).append(key)

        requests = []
        for keys in jobs.values():
            for start in range(0, len(keys), settings.max_questions):
                requests.append(keys[start:start + settings.max_questions])

        def run(keys):
            return self.send(keys, by_key)

        if len(requests) == 1:
            reports = [run(requests[0])]
        elif requests:
            contexts = [contextvars.copy_context() for _ in requests]
            with ThreadPoolExecutor(max_workers=min(settings.workers, len(requests))) as pool:
                futures = [pool.submit(ctx.run, run, keys) for ctx, keys in zip(contexts, requests)]
                reports = [f.result() for f in futures]
        else:
            reports = []

        for report in reports:
            self.emit(report)
        for key in order:  # one trace row per question, however many times it was written
            if key in used_ahead:
                continue
            group = by_key[key]
            only_ahead = all(lazy._ahead for lazy in group)
            shown = group[0] if only_ahead else next(lazy for lazy in group if not lazy._ahead)
            event = self.describe(shown)
            if only_ahead:
                event["ahead"] = True
                if shown._error is None:
                    if len(memo) > 512:
                        memo.clear()
                    memo[key] = (shown._answer, event["id"])
            for lazy in group:
                lazy._event_id = event["id"]
            self.emit(event)

    def send(self, keys, by_key):
        client = settings.client or JevClient()
        first = by_key[keys[0]][0]._question
        questions = {f"q{i}": by_key[key][0]._question.payload for i, key in enumerate(keys)}
        request_id = next(self.request_ids)
        started = time.perf_counter()
        report = {"type": "request", "id": request_id, "questions": len(keys)}
        try:
            response = client.ask(first.state, questions, settings.model)
            answers = response.get("answers") or {}
            for i, key in enumerate(keys):
                answer = answers.get(f"q{i}")
                if answer is None:
                    raise JevError(f"TypeSafe did not answer question {i + 1}")
                if settings.cache:
                    self.cache_put(key, answer)
                for lazy in by_key[key]:
                    lazy._answer, lazy._request, lazy._done = answer, request_id, True
            usage = response.get("usage") or {}
            report.update(model=response.get("model"), input_tokens=usage.get("input_tokens", 0),
                          output_tokens=usage.get("output_tokens", 0))
        except Exception as err:  # every judgment of this request fails the same way
            error = err if isinstance(err, JevError) else JevError(f"{type(err).__name__}: {err}")
            for key in keys:
                for lazy in by_key[key]:
                    lazy._error, lazy._request, lazy._done = error, request_id, True
            report["error"] = str(error)
        report["ms"] = round((time.perf_counter() - started) * 1000)
        report["cost_usd"] = report.get("input_tokens", 0) * PRICE_PER_MTOK_USD / 1_000_000
        with self.lock:
            self.totals["requests"] += 1
            self.totals["questions"] += len(keys)
            self.totals["input_tokens"] += report.get("input_tokens", 0)
            self.totals["output_tokens"] += report.get("output_tokens", 0)
            self.totals["ms"] += report["ms"]
        return report

    # trace ------------------------------------------------------------------ #

    def describe(self, lazy):
        q = lazy._question
        event = {
            "type": "judgment", "id": next(self.judgment_ids), "kind": q.kind,
            "file": os.path.basename(q.file or ""), "line": q.line, "source": q.source,
            "question": q.instructions, "criteria": q.criteria,
            "state": _preview(q.state_key), "cached": lazy._cached, "request": lazy._request,
        }
        with self.lock:
            self.totals["judgments"] += 1
            self.totals["cached"] += 1 if lazy._cached else 0
        if lazy._error is not None:
            event["error"] = str(lazy._error)
            return event
        answer = lazy._answer
        try:
            if q.kind == "noul":
                event.update(p=round(lazy.p, 4), verdict=lazy.verdict(), sure=sure_level())
            elif q.kind == "choice":
                event.update(choice=answer.get("choice"), confidence=answer.get("confidence"),
                             probabilities=lazy.probabilities)
            else:
                event.update(score=answer.get("score"), level=lazy.level.name,
                             confidence=answer.get("confidence"), probabilities=lazy.probabilities)
        except Exception as err:  # an answer in a shape we did not expect
            event["error"] = f"{type(err).__name__}: {err}"
        return event

    def emit(self, event):
        collector = _collector.get()
        if collector is not None:
            collector.append(event)
        path = os.environ.get("SEEMS_TRACE")
        if path:
            with self.lock:
                try:
                    with open(path, "a", encoding="utf-8") as handle:
                        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                except OSError:
                    pass


def _preview(text, limit=900):
    return text if len(text) <= limit else text[:limit] + " …"


_engine = _Engine()


class trace:
    """``with seems.trace() as events:`` collects every request and judgment."""

    def __enter__(self):
        self.events = []
        self._token = _collector.set(self.events)
        return self.events

    def __exit__(self, *exc):
        _collector.reset(self._token)
        return False


def stats() -> dict:
    """Totals for this process."""
    with _engine.lock:
        totals = dict(_engine.totals)
    totals["cost_usd"] = totals["input_tokens"] * PRICE_PER_MTOK_USD / 1_000_000
    return totals


def clear_cache():
    _engine.clear_cache()


def flush():
    """Send every waiting judgment of this thread now."""
    _engine.flush()


def each(items, fn, workers=None):
    """Run fn(item) for every item side by side and return the results in order.

    Use it for loops whose rounds do not depend on each other. Each round can ask
    its own judgments; the rounds wait for Jev at the same time.
    """
    items = list(items)
    if not items:
        return []
    contexts = [contextvars.copy_context() for _ in items]
    with ThreadPoolExecutor(max_workers=min(workers or settings.workers, len(items))) as pool:
        futures = [pool.submit(ctx.run, fn, item) for ctx, item in zip(contexts, items)]
        return [future.result() for future in futures]
