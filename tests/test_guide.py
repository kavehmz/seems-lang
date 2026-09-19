"""The language guide page is built from its source and every sample in it is valid Seems."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_guide  # noqa: E402


def test_docs_page_is_up_to_date():
    with open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8") as handle:
        assert handle.read() == build_guide.build(), "run: docker compose run --rm -T app python tools/build_guide.py > docs/index.html"


def test_samples_are_highlighted_like_the_playground():
    html = build_guide.highlight('if ticket.text asks for a refund:\n    pay()\nunsure:\n    ask()')
    assert '<span class="sx-verb">asks</span> <span class="sx-phrase">for a refund</span>' in html
    assert '<span class="sx-keyword">unsure</span>' in html


def test_a_bad_sample_breaks_the_build():
    import pytest
    with pytest.raises(SyntaxError):
        build_guide.highlight("if reply contradicts the policy:\n    pass")
