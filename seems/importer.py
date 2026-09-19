"""Run Seems files and let ``import`` find them.

After ``seems.install()`` a plain ``import desk`` loads ``desk.seems`` from
anywhere on ``sys.path``, next to ordinary ``.py`` modules.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import linecache
import os
import sys

from . import runtime
from .translator import compile_seems

SUFFIX = ".seems"


def module_globals(extra=None):
    """Names every Seems module gets for free."""
    names = {"__seems__": runtime, "Unsure": runtime.Unsure}
    names.update(extra or {})
    return names


class SeemsLoader(importlib.machinery.SourceFileLoader):
    def path_stats(self, path):
        # No bytecode cache: the translator decides what the code means.
        raise OSError("seems modules are not cached")

    def source_to_code(self, data, path, *, _optimize=-1):
        source = importlib.util.decode_source(data) if isinstance(data, bytes) else data
        return compile_seems(source, path)

    def exec_module(self, module):
        module.__dict__.update(module_globals())
        super().exec_module(module)


def install():
    """Teach ``import`` to load .seems files. Safe to call more than once."""
    if any(getattr(hook, "_seems", False) for hook in sys.path_hooks):
        return
    details = [
        (SeemsLoader, [SUFFIX]),
        (importlib.machinery.ExtensionFileLoader, importlib.machinery.EXTENSION_SUFFIXES),
        (importlib.machinery.SourceFileLoader, importlib.machinery.SOURCE_SUFFIXES),
        (importlib.machinery.SourcelessFileLoader, importlib.machinery.BYTECODE_SUFFIXES),
    ]
    hook = importlib.machinery.FileFinder.path_hook(*details)

    def seems_hook(path):
        return hook(path)

    seems_hook._seems = True
    sys.path_hooks.insert(0, seems_hook)
    sys.path_importer_cache.clear()


def run_source(source: str, filename: str = "<seems>", argv=None, name="__main__"):
    """Translate and run a program. Returns its globals."""
    install()
    linecache.cache[filename] = (len(source), None, source.splitlines(keepends=True), filename)
    code = compile_seems(source, filename)
    scope = module_globals({"__name__": name, "__file__": filename, "__builtins__": __builtins__})
    if argv is not None:
        sys.argv = list(argv)
    exec(code, scope)
    runtime.flush()
    return scope


def run_path(path: str, argv=None):
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    folder = os.path.dirname(os.path.abspath(path))
    if folder not in sys.path:
        sys.path.insert(0, folder)
    return run_source(source, path, argv=argv)
