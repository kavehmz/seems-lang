# Verification

Recorded on 2026-09-19 inside the container. The model that answered was `jev-1.13.0`
(alias `jev-latest`).

## Tests without network

```
docker compose run --rm app pytest        ->  72 passed
```

| File | What it proves |
| --- | --- |
| `tests/test_translator.py` | Plain Python comes out unchanged, including 120 standard library files and code that uses our words as variable names. Line numbers never move. Verb, phrase, condition, `unsure` and block rules. Helpful errors with the right line. |
| `tests/test_language.py` | Source to result with a fake Jev: three-valued logic, `Unsure`, certainty, exact "no" skips Jev, side effects never run early, lazy batching, asking ahead for `elif`, cache, trace, `seems.each`, `match`, async, generators, import hook, command line. |
| `tests/test_server.py` | Pages, translate API, run stream, local-only guard, and the support desk written in Seems. |

These tests prove the language mechanics. They say nothing about how well Jev judges.

## Live check

```
docker compose run --rm app python tools/live_check.py      (no cache)
```

| Example | Exit | Judgments | Unsure | Requests | Tokens | Cost USD | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 01_is_vs_seems | 0 | 10 | 2 | 4 | 1206 | 0.000051 | 3.10 |
| 02_support_triage | 0 | 32 | 3 | 8 | 4160 | 0.000175 | 0.97 |
| 03_unsure | 0 | 6 | 3 | 6 | 1695 | 0.000071 | 4.37 |
| 04_relations | 0 | 9 | 1 | 9 | 2820 | 0.000118 | 3.90 |
| 05_libraries | 0 | 30 | 5 | 20 | 7064 | 0.000297 | 2.39 |
| 06_more_python | 0 | 10 | 0 | 5 | 2153 | 0.000090 | 0.96 |

- One round trip takes 0.65 to 0.85 s. Example 02 routes 8 tickets with 32 judgments in about 1 s,
  because the tickets run side by side and each ticket needs one request.
- Examples 01, 03 and 04 loop one item after another on purpose, to stay simple. Their time is
  the number of round trips times about 0.75 s.
- The support desk page routes one ticket in about 0.7 s: four judgments, one request.

## What Jev did, including the odd parts

- **The mechanical questions work.** "Does this seem angry?", "Does this ask for a refund?",
  "Does `reply` contradict `policy`?" gave 0.88 to 0.99 on clear yes cases and 0.01 to 0.13 on clear no cases.
- **Unsure shows up where a person would hesitate.**
  - "I was charged twice for the same order. Can someone look into this?" asks for a refund: 0.72.
  - "The standing desk arrived. It wobbles a bit and I am not sure I want to keep it." asks for a refund: 0.26.
  - "Great product, shame about the delivery." seems happy: 0.35.
- **Answers are not perfectly repeatable.** The same request, sent three times, moved by up to
  0.05 (for example 0.69, 0.74, 0.74). A judgment that sits on the 0.75 line can flip between
  yes and unsure from run to run. Two fixes are built in: the cache makes a stored answer final,
  and the unsure band means a flip lands in "ask a person", not in the opposite action.
  The example texts were picked to sit away from the lines.
- **A loose phrase gives a loose answer.** "Returns are usually fine, but it can depend on the
  product." contradicts a 30-day policy with 0.69 to 0.75. A named `judgment` with yes and no
  criteria is the right tool when it matters.
- **Choice with a weak fit.** "THIS IS THE THIRD TIME I WRITE. Your app is garbage and I am
  leaving." went to team `other`, not `technical`. The text names no topic, so that is fair.
  The ticket still reached the priority queue through the anger scale.
- **Choice confidence.** "Do you have an API for exporting invoices?" split between `technical`
  0.59 and `billing` 0.41. The desk sent it to human review because no team was sure.

## Browser check

Checked in Chrome at 1480 x 940: example loading, live syntax errors while typing, run and stop,
streaming output, judgment rows with probability tracks and bars, "asked ahead" tags, gutter dots,
click a judgment to jump to its line, the translated Python tab, the guide tab, the certainty
slider, the desk form, ticket list, per-ticket trace and the desk source view. No console errors.
