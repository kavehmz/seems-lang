"""End-to-end: Seems source -> translator -> runtime, with a fake Jev."""
import json
import os
import subprocess
import sys
import textwrap

import pytest

import seems
from conftest import run
from seems.testing import FakeJev, choice, noul, score

DECLS = '''
kind team: "Which team should handle this?"
    billing: "payments, refunds"
    technical: "bugs, outages"
    other: "none of these"

scale anger: "How angry is the customer?"
    calm: "polite"
    annoyed: "complains, stays civil"
    furious: "insults or threats"

judgment urgent: "Does this need an answer within one hour?"
    yes: "outage, money lost"
    no: "can wait a day"
'''


def test_yes_no_and_unsure_branches(jev):
    scope = run('''
        def mood(text):
            if text seems angry:
                return "angry"
            else:
                return "fine"
            unsure:
                return "unsure"
        results = [mood("I am furious"), mood("hello"), mood("hmm")]
    ''')
    assert scope["results"] == ["angry", "fine", "unsure"]


def test_unhandled_unsure_raises(jev):
    with pytest.raises(seems.Unsure) as err:
        run('''
            if "hmm" seems angry:
                pass
        ''')
    assert "0.50" in str(err.value) and "unsure:" in str(err.value)


def test_unsure_can_be_caught_like_any_exception(jev):
    scope = run('''
        try:
            flag = bool("hmm" seems angry)
        except Unsure as err:
            flag = "caught"
            p = err.judgment.p
    ''')
    assert scope["flag"] == "caught" and scope["p"] == 0.5


def test_certainty_moves_the_bar(jev):
    jev.answer = lambda state, q: noul(0.8)
    scope = run('''
        import seems
        a = ("x" seems angry).verdict()
        with seems.certainty(0.9):
            b = ("x" seems angry).verdict()
    ''')
    assert (scope["a"], scope["b"]) == ("yes", "unsure")


def test_exact_no_never_calls_jev(jev):
    scope = run('''
        amount = 10
        text = "please refund me"
        hit = False
        if amount > 500 and text asks for a refund:
            hit = True
    ''')
    assert scope["hit"] is False
    assert jev.requests == []


def test_exact_no_after_a_judgment_cancels_the_request(jev):
    run('''
        amount = 10
        if "refund please" asks for a refund and amount > 500:
            pass
    ''')
    assert jev.requests == []


def test_judgments_in_one_condition_share_a_request(jev):
    scope = run('''
        text = "I am furious, refund me"
        ok = False
        if text seems angry and text asks for a refund:
            ok = True
    ''')
    assert scope["ok"] is True
    assert len(jev.requests) == 1 and len(jev.requests[0]["questions"]) == 2


def test_different_states_go_out_side_by_side(jev):
    scope = run('''
        a, b = "I am furious", "refund please"
        ok = bool(a seems angry and b asks for a refund)
    ''')
    assert len(jev.requests) == 2  # two states, sent together in one round


def test_side_effects_wait_for_the_judgment(jev):
    scope = run('''
        calls = []
        def charge():
            calls.append(1)
            return True
        if "hello" seems angry and charge():
            pass
        if "I am furious" seems angry and charge():
            pass
    ''')
    assert scope["calls"] == [1]


def test_three_valued_logic(jev):
    scope = run('''
        r = {}
        r["no_and_unsure"] = "no"
        if "hello" seems angry and "hmm" seems angry:
            r["no_and_unsure"] = "yes"
        unsure:
            r["no_and_unsure"] = "unsure"

        if "I am furious" seems angry or "hmm" seems angry:
            r["yes_or_unsure"] = "yes"
        unsure:
            r["yes_or_unsure"] = "unsure"

        if "I am furious" seems angry and "hmm" seems angry:
            r["yes_and_unsure"] = "yes"
        unsure:
            r["yes_and_unsure"] = "unsure"

        if not "hello" seems angry:
            r["not_no"] = "yes"
    ''')
    assert scope["r"] == {"no_and_unsure": "no", "yes_or_unsure": "yes",
                          "yes_and_unsure": "unsure", "not_no": "yes"}


def test_elif_is_skipped_once_the_chain_is_unsure(jev):
    scope = run('''
        seen = []
        def note():
            seen.append("elif ran")
            return True
        if "hmm" seems angry:
            r = 1
        elif note():
            r = 2
        else:
            r = 3
        unsure:
            r = "unsure"
    ''')
    assert scope["r"] == "unsure" and scope["seen"] == []


def test_lazy_values_are_sent_together(jev):
    scope = run(DECLS + '''
texts = ["billing: I am furious", "technical: hello", "other: refund"]
owners = [team(t) for t in texts]
moods = [anger(t) for t in texts]
flags = [t asks for a refund for t in texts]
names = [o.name for o in owners]          # the first use sends everything
levels = [str(m) for m in moods]
refunds = [bool(f) for f in flags]
''')
    assert scope["names"] == ["billing", "technical", "other"]
    assert scope["levels"] == ["furious", "calm", "calm"]
    assert scope["refunds"] == [False, False, True]
    # three texts -> three requests, each with all three questions about that text
    assert len(jev.requests) == 3 and all(len(r["questions"]) == 3 for r in jev.requests)


def test_kind_compares_with_three_values(jev):
    jev.answer = lambda s, q: choice({"billing": 0.55, "technical": 0.40, "other": 0.05})
    scope = run(DECLS + '''
pick = team("x")
top, sure = pick.name, pick.sure
try:
    same = bool(pick == team.billing)
except Unsure:
    same = "unsure"
definitely_not = bool(pick != team.other)
either = bool(pick.is_one_of(team.billing, team.technical))
''')
    assert scope["top"] == "billing" and scope["sure"] is False
    assert scope["same"] == "unsure"
    assert scope["definitely_not"] is True and scope["either"] is True


def test_match_statement_on_a_kind(jev):
    scope = run(DECLS + '''
match team("there is a bug, technical"):
    case team.billing:
        r = "b"
    case team.technical:
        r = "t"
''')
    assert scope["r"] == "t"


def test_scale_compares_with_levels(jev):
    jev.answer = lambda s, q: score([0.05, 0.15, 0.80])
    scope = run(DECLS + '''
mood = anger("x")
a = bool(mood >= anger.annoyed)      # 0.95
b = (mood >= anger.furious).verdict()  # 0.80
c = (mood == anger.calm).verdict()     # 0.05
d = round(float(mood), 2)
e = sorted([anger("y"), mood], key=float)[0] is not None
f = round(mood.normalized, 3)
''')
    assert (scope["a"], scope["b"], scope["c"], scope["d"]) == (True, "yes", "no", 1.75)
    assert scope["f"] == 0.875


def test_named_judgment_is_used_by_seems(jev):
    scope = run(DECLS + '''
class Ticket:
    def __init__(self, text): self.text = text
ticket = Ticket("total outage since 9am")
a = bool(ticket.text seems urgent)
b = bool(urgent("just a question"))
''')
    assert scope["a"] is True and scope["b"] is False
    sent = jev.requests[0]
    assert sent["state"] == "total outage since 9am"
    question = list(sent["questions"].values())[0]
    assert question["instructions"] == "Does this need an answer within one hour?"
    assert question["criteria"] == {"true": "outage, money lost", "false": "can wait a day"}


def test_questions_follow_the_docs_shape(jev):
    run(DECLS + '''
class T: pass
ticket = T(); ticket.text = "refund"; policy = "Refunds within 30 days"
rows = [{"body": "refund"}]
a = ticket.text asks for a refund
b = ticket.text contradicts policy
c = rows[0]["body"] mentions "a refund?"
d = team(ticket.text)
e = anger(text=ticket.text, policy=policy)
seems_flush = __seems__.flush()
''')
    by_type = {}
    for request in jev.requests:
        for q in request["questions"].values():
            by_type.setdefault(q["type"], []).append((request["state"], q))
    nouls = {q["instructions"]: state for state, q in by_type["noul"]}
    assert nouls["Does this ask for a refund?"] == "refund"
    assert nouls["Does `ticket.text` contradict `policy`?"] == \
        {"ticket": {"text": "refund"}, "policy": "Refunds within 30 days"}
    assert nouls["Does this mention a refund?"] == "refund"
    state, q = by_type["choice"][0]
    assert state == "refund" and q["criteria"]["billing"] == "payments, refunds"
    state, q = by_type["score"][0]
    assert state == {"text": "refund", "policy": "Refunds within 30 days"}
    assert q["criteria"] == ["polite", "complains, stays civil", "insults or threats"]


def test_cache_makes_a_second_ask_free(jev):
    seems.configure(cache=True)
    run('''
        a = bool("I am furious" seems angry)
        b = bool("I am furious" seems angry)
    ''')
    assert jev.question_count == 1


def test_disk_cache_survives_between_runs(jev, tmp_path, monkeypatch):
    monkeypatch.setenv("SEEMS_CACHE", str(tmp_path / "cache.sqlite"))
    seems.configure(cache=True)
    run('a = bool("I am furious" seems angry)')
    seems.runtime._engine.memory.clear()
    run('a = bool("I am furious" seems angry)')
    assert jev.question_count == 1


def test_trace_lists_requests_and_judgments(jev):
    with seems.trace() as events:
        run(DECLS + '''
t = "billing: I am furious"
if t seems angry and team(t) == team.billing:
    pass
''')
    kinds = [e["type"] for e in events]
    assert kinds.count("request") == 1 and kinds.count("judgment") == 2
    judgment = next(e for e in events if e.get("kind") == "noul")
    assert judgment["verdict"] == "yes" and judgment["line"] > 0
    assert judgment["question"] == "Does this seem angry?"


def test_each_runs_rounds_side_by_side(jev):
    scope = run('''
        import seems
        def check(text):
            return "angry" if text seems angry else "fine"
        out = seems.each(["I am furious", "hello", "furious again"], check)
    ''')
    assert scope["out"] == ["angry", "fine", "angry"]


def test_python_features_keep_working(jev):
    scope = run('''
        import asyncio, dataclasses, functools, re
        from collections import Counter

        @dataclasses.dataclass
        class Review:
            text: str
            @functools.cached_property
            def angry(self):
                return bool(self.text seems angry)

        def stream(items):
            for item in items:
                if item.angry:
                    yield item.text.upper()

        async def main():
            await asyncio.sleep(0)
            return [r async for r in agen()]

        async def agen():
            for r in stream([Review("I am furious"), Review("hello")]):
                yield r

        out = asyncio.run(main())
        counts = Counter(w for w in re.findall(r"\\w+", "a b a"))
    ''')
    assert scope["out"] == ["I AM FURIOUS"] and scope["counts"]["a"] == 2


def test_errors_point_at_the_line_the_programmer_wrote(jev):
    with pytest.raises(ZeroDivisionError) as err:
        run('''
            kind team: "q"
                a: "x"
                b: "y"
            if "I am furious" seems angry:
                1 / 0
        ''')
    tb = err.traceback[-1]
    assert tb.lineno + 1 == 6  # pytest counts from 0


def test_api_failure_reaches_the_program(jev):
    def boom(state, q):
        raise RuntimeError("network down")
    jev.answer = boom
    with pytest.raises(seems.JevError) as err:
        run('x = bool("a" seems angry)')
    assert "network down" in str(err.value)


def test_import_hook_and_cli(jev, tmp_path):
    (tmp_path / "rules.seems").write_text(textwrap.dedent('''
        def is_angry(text):
            return text seems angry
    '''))
    (tmp_path / "main.seems").write_text(textwrap.dedent('''
        import rules
        print("loaded", rules.__name__, rules.__file__.endswith("rules.seems"))
    '''))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PYTHONPATH": root}
    done = subprocess.run([sys.executable, "-m", "seems", "run", str(tmp_path / "main.seems")],
                          capture_output=True, text=True, env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "loaded rules True"

    done = subprocess.run([sys.executable, "-m", "seems", "translate", "--standalone",
                           str(tmp_path / "rules.seems")], capture_output=True, text=True, env=env)
    assert "import seems.runtime as __seems__" in done.stdout

    (tmp_path / "bad.seems").write_text("x = 1\nunsure:\n    pass\n")
    done = subprocess.run([sys.executable, "-m", "seems", "check", str(tmp_path / "bad.seems")],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 1 and "line 2" in done.stderr and "unsure" in done.stderr


# ---- asking ahead: one round trip for a whole if/elif statement ------------- #

def test_elif_judgments_travel_with_the_if(jev):
    scope = run('''
        text = "please refund me"
        if text seems angry:
            r = "angry"
        elif text asks for a refund:
            r = "refund"
        else:
            r = "other"
    ''')
    assert scope["r"] == "refund"
    assert len(jev.requests) == 1 and len(jev.requests[0]["questions"]) == 2


def test_ahead_judgments_are_dropped_when_an_exact_branch_wins(jev):
    scope = run('''
        text, vip = "please refund me", True
        if vip:
            r = "vip"
        elif text seems angry:
            r = "angry"
        elif text asks for a refund:
            r = "refund"
        after = "hello" seems angry
        done = bool(after)
    ''')
    assert scope["r"] == "vip"
    assert jev.question_count == 1  # only the judgment after the statement


def test_ahead_answers_are_used_even_without_the_cache(jev):
    with seems.trace() as events:
        scope = run('''
            text = "please refund me"
            if text seems angry:
                r = 1
            elif text asks for a refund:
                r = 2
        ''')
    assert scope["r"] == 2 and jev.question_count == 2 and len(jev.requests) == 1
    ahead = [e for e in events if e.get("ahead")]
    used = [e for e in events if e["type"] == "used"]
    assert len(ahead) == 1 and used == [{"type": "used", "id": ahead[0]["id"], "line": 5}]
    assert len([e for e in events if e["type"] == "judgment"]) == 2


def test_statement_without_unsure_branch_still_raises(jev):
    with pytest.raises(seems.Unsure):
        run('''
            text = "hmm"
            if text seems angry:
                r = 1
            elif text asks for a refund:
                r = 2
        ''')


def test_a_name_that_only_exists_later_does_not_break_asking_ahead(jev):
    scope = run('''
        text = "please refund me"
        if (n := len(text)) > 100 and text seems angry:
            r = 1
        elif later_name if False else text asks for a refund:
            r = 2
    ''')
    assert scope["r"] == 2
