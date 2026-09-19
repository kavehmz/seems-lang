"""A stand-in for Jev, for tests and offline work.

    fake = FakeJev(lambda state, question: noul(0.9))
    seems.configure(client=fake, cache=False)
"""
from __future__ import annotations


def noul(p):
    return {"type": "noul", "noul": p}


def choice(probabilities: dict):
    top = max(probabilities, key=probabilities.get)
    return {"type": "choice", "choice": top, "probabilities": probabilities,
            "confidence": probabilities[top]}


def score(probabilities: list):
    value = sum(i * p for i, p in enumerate(probabilities))
    return {"type": "score", "score": value, "confidence": max(probabilities),
            "probabilities": {str(i): p for i, p in enumerate(probabilities)},
            "legend": {}}


class FakeJev:
    """answer(state, question) -> one of noul(), choice(), score()."""

    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    def ask(self, state, questions, model):
        self.requests.append({"state": state, "questions": questions, "model": model})
        answers = {qid: self.answer(state, q) for qid, q in questions.items()}
        return {"model": "fake-jev", "answers": answers,
                "usage": {"input_tokens": 100, "output_tokens": 10}}

    @property
    def question_count(self):
        return sum(len(r["questions"]) for r in self.requests)
