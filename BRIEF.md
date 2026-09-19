# Brief: a programming language with judgment built in

This is the prompt the project started from. It records what we agreed to build, not how.

## The idea

Create a real programming language where a condition can be written in plain
English and answered by TypeSafe Jev.

```
if order.total > 500 and ticket.text asks for a refund:
    send_to_manager(ticket)
unsure:
    send_to_human(ticket)
```

- `order.total > 500` is exact. Code answers it.
- `ticket.text asks for a refund` is a judgment. Jev answers it with a probability.

## What we want

1. **A language, not a prompt.** The grammar is strict. The parser uses no AI.
   English appears only after a fixed judgment verb. A typo is a syntax error.
2. **Jev sits inside the conditionals.** The three Jev primitives become language
   features: Noul is a yes/no judgment, Choice is a `kind`, Score is a `scale`.
3. **Uncertainty is part of the language.** A judgment is yes, no or unsure.
   `if` gets an `unsure:` branch. An unhandled unsure raises an exception.
4. **A mature language underneath.** Use the TypeScript trick: the language is a
   superset of Python. Every Python program is valid. Every pip library, class,
   exception, test tool and debugger keeps working.
5. **Normal abilities, not a toy.** Show a real program: a web service with a
   database, written in the language.
6. **Fast by default.** Judgments that do not depend on each other go to Jev together.
7. **Everything visible.** Show the source, the translated Python and every judgment
   with its probability, cost and time.

## Rules for the build

- Runs in a container. Nothing is installed on the host.
- The API key comes from `.env`. It never reaches the browser.
- Follow the live TypeSafe docs for questions, state and confidence.
- Keep rules, math, counting and dates in code. Jev only judges meaning.
- An original design.
