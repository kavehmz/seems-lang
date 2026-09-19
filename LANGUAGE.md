# The Seems language

Seems is a superset of Python 3.13. Every Python program is a valid Seems program.
This page covers only what Seems adds.

## 1. One rule

- Python operators are **exact**. They run in code: `>`, `==`, `in`, `is`, `len(...)`.
- A **judgment verb** sends a question to TypeSafe Jev and gets a probability back.

```python
if review.stars >= 4 and review.text seems happy:
    ...
```

## 2. Judgment verbs

### Describing verbs: `value VERB english`

`seems` `sounds` `looks` `mentions` `discusses` `describes` `expresses` `suggests` `asks` `requests`

```python
ticket.text asks for a refund
item.title seems like clickbait
log_line suggests a memory leak
```

- The value on the left is the thing Jev looks at. It is a name, an attribute, an index or a
  call: `rows[0]["body"].strip() seems rude`. Put anything larger in brackets: `(a + b) seems long`.
- The English runs until `and`, `or`, `if`, `else`, a comma, a closing bracket, a colon or the
  end of the line. Inside brackets it also ends at the `for` of a comprehension.
- Put the English in quotes when it needs one of those words, an apostrophe or other
  punctuation: `text seems "rude and dismissive"`. An f-string works too:
  `text mentions f"the product {name}"`.
- The question Jev gets is mechanical: `X asks for a refund` becomes
  *"Does this ask for a refund?"* with `X` as the state.

### Relating verbs: `value VERB value`

`contradicts` `supports` `answers` `means` `matches`

```python
reply contradicts policy
reply answers ticket.question
a means b                                  # equality by meaning. == stays exact
reply contradicts "all sales are final"    # English goes in quotes
```

Jev gets both values as named state and a question like *"Does `reply` contradict `policy`?"*.

### What a judgment is

A judgment is a value. It can live in a variable, a list or a property.

| Use | Result |
| --- | --- |
| `if j:` `bool(j)` `not j` | True or False. Raises `Unsure` when Jev is not sure. |
| `j.p` | The probability of yes, 0 to 1. Never raises. |
| `j.verdict()` | `"yes"`, `"no"` or `"unsure"`. Never raises. |
| `j.yes` `j.no` `j.unsure` `j.sure` | Plain booleans. Never raise. |
| `a & b`, `a \| b`, `~a` | Combine judgments without forcing an answer. |

## 3. Unsure

A probability at or above the certainty level is **yes**. At or below one minus the level is
**no**. Between is **unsure**. The default level is 0.75.

```python
if text asks for a refund:
    pay_out()
else:
    close()
unsure:
    ask_a_person()
```

- `unsure:` is the last branch of an `if`. It runs when the condition cannot be decided.
- Without an `unsure:` branch, an unsure judgment raises `Unsure`. Catch it like any exception:
  `except Unsure as err:` and read `err.judgment.p`.
- `and`, `or`, `not` follow three-valued logic. *no and unsure* is no. *yes or unsure* is yes.
  *yes and unsure* is unsure.
- `with seems.certainty(0.95):` raises the level inside the block. `seems.configure(sure=0.9)`
  sets it for the program.

## 4. Declarations

```python
kind team: "Which team should handle this support ticket?"
    billing:   "payments, charges, invoices, refunds"
    technical: "bugs, errors, outages, integrations"
    other:     "none of the other teams fit"

scale anger: "How angry is the customer?"
    calm:    "polite or neutral, no complaint"
    annoyed: "complains, but stays civil"
    furious: "insults, threats, or shouting in capitals"

judgment urgent: "Does this ticket need an answer within one hour?"
    yes: "an outage, lost money, or a legal deadline"
    no:  "a general question or feedback"
```

| Block | Jev primitive | Call it | You get |
| --- | --- | --- | --- |
| `kind` | Choice | `team(text)` | a pick: `.name`, `.top`, `.p("billing")`, `.probabilities`, `.confidence`, `.sure`, `.is_one_of(a, b)` |
| `scale` | Score | `anger(text)` | a rating: `.level`, `.score`, `.normalized`, `.probabilities`, `.confidence`, `float(r)` |
| `judgment` | Noul with criteria | `urgent(text)` or `text seems urgent` | a judgment |

- A description is optional: a bare `other` is allowed. A name with spaces goes in quotes.
- A kind needs 2 to 255 entries. A scale needs 2 to 10 levels, lowest first.
- Add a "none of these" entry when nothing may fit. Jev can only pick what you list.
- Write scale levels as situations ("insults or threats"), not degrees ("very angry").
- Comparisons are three-valued, like judgments:
  - `team(text) == team.billing` uses the probability of `billing`.
  - `anger(text) >= anger.annoyed` uses the probability that the level is `annoyed` or higher.
  - `match team(text):` with `case team.billing:` works.
- One word after a describing verb may name a judgment: `text seems urgent` uses the declared
  question and criteria. Any other word is plain English.
- To give Jev several named parts, use keywords: `team(text=ticket.text, policy=policy)`.
  The question can then point at a part with backticks: ``"Does `policy` allow this?"``.
- `seems.ask("Is this a greeting?", text)` is a one-off yes/no judgment with your own question.

## 5. Speed

One round trip to Jev takes most of a second. The language keeps round trips few.

- **Lazy.** Creating a judgment sends nothing. The first time the program needs any answer,
  everything waiting is sent. Questions about the same value share one request. Other requests
  run side by side.

  ```python
  owner, mood, refund = team(text), anger(text), text asks for a refund   # nothing sent
  if refund: ...                                                           # one request, three questions
  ```
- **Conditions.** All judgments in one `and` / `or` condition go together.
- **if / elif.** The judgments of the later branches travel with the first one. Answers for
  branches that are not reached are ignored. The playground marks them "asked ahead".
- **Exact first.** In `amount > 500 and text asks for a refund`, a false `amount > 500` means
  Jev is never called.
- **No surprises.** A part of a condition that calls a function (`and charge(card)`) never runs
  early. It waits for the judgments before it, as in Python.
- **Loops.** A plain `for` loop asks round after round. For independent items use either form:

  ```python
  results = seems.each(tickets, route)                  # rounds run side by side, results in order
  flags = [t.text seems angry for t in tickets]         # or: build judgments first, read them later
  ```
- **Cache.** Answers are stored by model, state and question. Set `SEEMS_CACHE=/path/file.sqlite`
  to keep them between runs. `--no-cache` or `seems.configure(cache=False)` asks again.

## 6. Tools

```sh
python -m seems run program.seems [args]      # --stats, --trace FILE, --no-cache, --sure 0.9
python -m seems translate program.seems       # print the Python. --standalone adds the import line
python -m seems check program.seems           # syntax only
```

```python
import seems
seems.install()          # after this, `import desk` finds desk.seems next to .py files
with seems.trace() as events:    # every request and judgment, as dictionaries
    route(ticket)
seems.stats()            # totals: judgments, requests, tokens, cost
```

- A `.seems` module gets two names for free: `Unsure` and the hidden runtime `__seems__`.
- For tests, `seems.testing.FakeJev` stands in for the API. See `tests/conftest.py`.
- Settings come from the environment: `TYPESAFE_API_KEY` (or `TYPESAFE_API`), `TYPESAFE_MODEL`
  (default `jev-latest`; pin `jev-1.13.0` to keep tuned levels stable), `SEEMS_CACHE`, `SEEMS_TRACE`.

## 7. Keep in code

The TypeSafe docs list what Jev is weak at. Seems does not hide that.

- Math, counting, number and date comparisons: use Python.
- Text generation: Jev does not write. Use it to choose, not to produce.
- Jev reads literally. Write the exact condition. Put edge cases in `yes:` and `no:` criteria.
- Send only what the question needs. A judgment's state is just the value on its left.
- The certainty level is a policy choice. Test it on your own data before trusting it.
