import glob
import os
import sysconfig
import textwrap

import pytest

from seems.translator import SeemsSyntaxError, translate


def py(source):
    return translate(textwrap.dedent(source)).python


# ---- promise 1: valid Python is untouched -------------------------------- #

def test_plain_python_is_unchanged():
    source = "import os\nmatches = [m for m in os.listdir('.')]\nif matches and answers:\n    pass\n"
    assert translate(source).python == source
    assert translate(source).changed == []


def test_python_that_uses_our_words_as_names_is_unchanged():
    source = textwrap.dedent('''
        seems = 1; mentions = [2]; answers = {"a": 1}; unsure: int = 3; kind: str = "x"
        scale = seems + mentions[0]
        def matches(means, supports=None): return means if supports else answers
        match answers:
            case {"a": asks} if asks: print(asks, seems)
        for looks in mentions: print(looks)
        x = seems if unsure else scale
        print(f"{seems} {mentions!r}", matches(1), kind)
    ''')
    assert translate(source).python == source
    exec(compile(source, "<t>", "exec"), {})


def test_standard_library_survives_unchanged():
    stdlib = sysconfig.get_paths()["stdlib"]
    files = sorted(glob.glob(os.path.join(stdlib, "*.py")))[:120]
    assert len(files) > 50
    for path in files:
        with open(path, encoding="utf-8", errors="ignore") as handle:
            source = handle.read().replace("\r\n", "\n").replace("\r", "\n")
        try:
            compile(source, path, "exec")
        except (SyntaxError, ValueError):
            continue
        assert translate(source, path).python == source, path


# ---- promise 2: line numbers never move ---------------------------------- #

def test_line_count_is_kept():
    source = textwrap.dedent('''
        kind team: "Which team?"
            billing: "money"
            # a comment inside the block

            technical: "bugs"
        if text seems angry and n > 3:
            pass
        elif text mentions a refund:
            pass
        else:
            pass
        unsure:
            pass
    ''')
    result = translate(source)
    assert result.python.count("\n") == source.count("\n")
    compile(result.python, "<t>", "exec")


# ---- judgment verbs -------------------------------------------------------- #

def test_describing_verb():
    assert py("ok = ticket.text seems angry\n") == \
        "ok = __seems__.noul('seems', (ticket.text), 'ticket.text', 'angry', lambda: angry)\n"


def test_phrase_runs_to_the_colon():
    out = py("if msg asks for a refund in cash:\n    pass\n")
    assert "'for a refund in cash')" in out and out.rstrip().endswith("pass")


def test_phrase_stops_at_and_or_if_else_comma_bracket():
    assert "'angry'" in py("x = a seems angry and b\n")
    assert "'angry'" in py("x = a seems angry or b\n")
    assert "'angry'" in py("x = 1 if a seems angry else 2\n")
    assert "'angry'" in py("x = f(a seems angry, 2)\n")
    assert "'angry'" in py("x = [a seems angry]\n")


def test_quoted_phrase_may_contain_anything():
    out = py('x = a seems "rude and dismissive, or worse"\n')
    assert '"rude and dismissive, or worse")' in out


def test_fstring_phrase_is_passed_through():
    out = py('x = a mentions f"the product {name}"\n')
    assert 'f"the product {name}")' in out


def test_brackets_inside_a_phrase():
    out = py("x = (a seems like a complaint (strong one)).p\n")
    assert "'like a complaint (strong one)'" in out


def test_subject_is_a_primary_expression():
    out = py("x = rows[0].get('body').strip() seems angry\n")
    assert "(rows[0].get('body').strip())" in out
    out = py("x = not a.b seems angry\n")
    assert out.startswith("x = not __seems__.noul('seems', (a.b),")
    out = py("x = (a + b) seems long\n")
    assert "((a + b))" in out


def test_comprehension_for_ends_the_phrase():
    out = py("flags = [t.text asks for a refund for t in tickets]\n")
    assert "'for a refund')" in out and out.rstrip().endswith("for t in tickets]")
    out = py("flags = [t.text asks for a refund in cash for i, t in enumerate(tickets)]\n")
    assert "'for a refund in cash')" in out


def test_relating_verb_takes_two_values():
    assert py("x = reply contradicts policy.text\n") == \
        "x = __seems__.relate('contradicts', (reply), 'reply', (policy.text), 'policy.text')\n"
    assert '("all sales are final")' in py('x = reply contradicts "all sales are final"\n')


def test_relating_verb_rejects_bare_english():
    with pytest.raises(SeemsSyntaxError) as err:
        py("x = reply contradicts the policy\n")
    assert "compares two values" in str(err.value)
    assert err.value.lineno == 1


def test_verb_inside_the_subject_of_another():
    out = py("x = str(a seems b) seems c\n")
    assert out.count("__seems__.noul") == 2
    compile(out, "<t>", "exec")


def test_non_ascii_text_before_a_verb():
    out = py('x = "héllo wörld ✓"; y = t seems angry\n')
    assert out.startswith('x = "héllo wörld ✓"; y = __seems__.noul(')


def test_apostrophe_gives_a_helpful_error():
    with pytest.raises(SeemsSyntaxError) as err:
        py("if t seems like the customer's fault:\n    pass\n")
    assert "quotes" in str(err.value)


# ---- conditions ------------------------------------------------------------ #

def test_and_is_asked_together():
    out = py("if t.amount > 5 and t.text seems angry and t.text mentions a refund:\n    pass\n")
    assert out.startswith("if __seems__.all_(lambda: (t.amount > 5), lambda: (__seems__.noul(")
    assert "spec=(1, 1, 1))" in out


def test_calls_are_never_run_early():
    out = py("if t seems angry and charge(card):\n    pass\n")
    assert "spec=(1, 0))" in out


def test_or_and_not_and_groups():
    out = py("if not a seems x or (b seems y and c):\n    pass\n")
    assert "__seems__.any_(lambda: (__seems__.not_(" in out
    assert "__seems__.all_(" in out
    compile(out, "<t>", "exec")


def test_ternary_condition_is_left_alone():
    out = py("if a seems x and b if c else d:\n    pass\n")
    assert "all_" not in out
    compile(out, "<t>", "exec")


def test_plain_condition_is_left_alone():
    source = "if a and b or not c:\n    pass\n"
    assert py(source) == source


def test_while_header():
    out = py("while queue and queue[0] seems urgent:\n    queue.pop(0)\n")
    assert out.startswith("while __seems__.all_(")


# ---- unsure ---------------------------------------------------------------- #

def test_unsure_chain():
    out = py('''
        if a seems x:
            r = 1
        elif b > 2:
            r = 2
        else:
            r = 3
        unsure:
            r = 4
    ''')
    lines = out.strip().split("\n")
    assert lines[0] == "if (_seems_u2 := __seems__.Chain()).test(lambda: (__seems__.noul('seems', (a), 'a', 'x', lambda: x))):"
    assert lines[2] == "elif _seems_u2.test(lambda: (b > 2)):"
    assert lines[4] == "elif _seems_u2.sure:"
    assert lines[6] == "elif _seems_u2.unsure:"


def test_unsure_without_else():
    out = py("if a seems x:\n    pass\nunsure:\n    pass\n")
    assert "elif _seems_u1.unsure:" in out and ".sure:" not in out


def test_nested_chains_do_not_mix():
    out = py('''
        if a seems x:
            if b seems y:
                pass
            unsure:
                pass
        else:
            pass
    ''')
    assert "_seems_u3.unsure" in out
    assert "else:" in out and "_seems_u2" not in out


def test_else_of_a_loop_is_not_part_of_the_chain():
    out = py('''
        if a seems x:
            pass
        for i in y:
            pass
        else:
            pass
    ''')
    assert "\nelse:" in out


def test_unsure_needs_an_if():
    with pytest.raises(SeemsSyntaxError) as err:
        py("x = 1\nunsure:\n    pass\n")
    assert err.value.lineno == 2


def test_unsure_must_be_last():
    with pytest.raises(SeemsSyntaxError):
        py("if a seems x:\n    pass\nunsure:\n    pass\nelse:\n    pass\n")


def test_walrus_condition_in_a_chain_is_not_wrapped_in_a_lambda():
    out = py("if (n := len(a)) > 3 and a seems long:\n    pass\nunsure:\n    pass\n")
    assert ".check(" in out and ".test(lambda" not in out and "all_" not in out
    compile(out, "<t>", "exec")


# ---- declarations ---------------------------------------------------------- #

def test_kind_block():
    out = py('''
        kind team: "Which team?"
            billing: "money"
            "credit card": "cards"
            other
    ''')
    assert 'team = __seems__.kind(\'team\', "Which team?", [' in out
    assert "('billing', \"money\")," in out
    assert '("credit card", "cards"),' in out
    assert "('other', None)])" in out


def test_blocks_work_inside_functions():
    out = py('''
        def make():
            scale heat: "How hot?"
                cold: "ice"
                hot: "fire"
            return heat
    ''')
    compile(out, "<t>", "exec")
    assert "    heat = __seems__.scale('heat'" in out


def test_block_errors():
    with pytest.raises(SeemsSyntaxError):
        py('kind team: "q"\n    only_one: "x"\n')
    with pytest.raises(SeemsSyntaxError):
        py('judgment urgent: "q"\n    maybe: "x"\n')
    with pytest.raises(SeemsSyntaxError):
        py('kind team: "q"\n    a: "x"\n    a: "y"\n')


def test_changed_lines_and_declared_names():
    result = translate('kind team: "q"\n    a: "x"\n    b: "y"\nx = 1\ny = t seems z\n')
    assert result.changed == [1, 2, 3, 5]
    assert result.declared == ["team"]


def test_standalone_header_goes_after_future_imports():
    out = translate('"""doc"""\nfrom __future__ import annotations\nx = t seems y\n', standalone=True).python
    assert out.split("\n")[2].startswith("import seems.runtime as __seems__")
