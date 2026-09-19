import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import seems  # noqa: E402
from seems import runtime  # noqa: E402
from seems.testing import FakeJev, choice, noul, score  # noqa: E402


def default_answer(state, question):
    """A tiny rule-based Jev: looks for marker words in the state text."""
    text = str(state).lower()
    kind = question["type"]
    if kind == "noul":
        ask = question["instructions"].lower()
        if "refund" in ask:
            return noul(0.97 if "refund" in text or "money back" in text else 0.02)
        if "angry" in ask:
            if "furious" in text:
                return noul(0.98)
            return noul(0.5 if "hmm" in text else 0.03)
        if "contradict" in ask:
            return noul(0.95 if "never" in text else 0.04)
        if "one hour" in ask:
            return noul(0.9 if "outage" in text else 0.05)
        return noul(0.5)
    if kind == "choice":
        options = list(question["criteria"])
        hit = next((o for o in options if o in text), None)
        if hit is None:
            return choice({o: 1 / len(options) for o in options})
        return choice({o: (0.94 if o == hit else 0.06 / (len(options) - 1)) for o in options})
    levels = len(question["criteria"])
    top = "furious" in text
    probs = [0.0] * levels
    probs[-1 if top else 0] = 0.9
    probs[-2 if top else 1] += 0.1
    return score(probs)


@pytest.fixture
def jev(monkeypatch):
    monkeypatch.delenv("SEEMS_CACHE", raising=False)
    monkeypatch.delenv("SEEMS_TRACE", raising=False)
    fake = FakeJev(default_answer)
    old = (runtime.settings.client, runtime.settings.cache, runtime.settings.sure)
    seems.configure(client=fake, cache=False, sure=0.75)
    runtime.clear_cache()
    yield fake
    runtime.settings.client, runtime.settings.cache, runtime.settings.sure = old
    runtime._engine.local.queue = []


def run(source, **names):
    """Run Seems source and return its globals."""
    import textwrap
    return seems.run_source(textwrap.dedent(source), "<test>", name="seems_test")
