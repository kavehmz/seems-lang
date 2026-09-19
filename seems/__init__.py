"""Seems: Python plus judgments.

    if ticket.text asks for a refund and ticket.amount > 500:
        ...
    unsure:
        ...

Use ``python -m seems run program.seems`` or ``seems.install()`` followed by a
normal ``import`` of a ``.seems`` file.
"""
from .client import JevClient, JevError
from .importer import install, run_path, run_source
from .runtime import (NO, UNSURE, YES, JudgmentDef, Kind, Level, Member, Pick, Rating, Scale,
                      Truth, Unsure, ask, certainty, clear_cache, configure, each, flush,
                      stats, sure_level, trace)
from .translator import SeemsSyntaxError, compile_seems, translate

__version__ = "0.1.0"

__all__ = [
    "JevClient", "JevError", "install", "run_path", "run_source", "NO", "UNSURE", "YES",
    "JudgmentDef", "Kind", "Level", "Member", "Pick", "Rating", "Scale", "Truth", "Unsure",
    "ask", "certainty", "clear_cache", "configure", "each", "flush", "stats", "sure_level",
    "trace", "SeemsSyntaxError", "compile_seems", "translate",
]
