"""Demo server: the playground API, the static pages and the support desk.

The desk itself lives in desk.seems and is loaded with a normal `import`.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

from flask import Flask, Response, abort, jsonify, request, send_from_directory

import seems
from seems.client import api_key_from_env
from seems.runtime import PRICE_PER_MTOK_USD, settings
from seems.translator import DESCRIBING, RELATING, translate

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(APP_DIR)
EXAMPLES_DIR = os.path.join(ROOT, "examples")
STATIC_DIR = os.path.join(APP_DIR, "static")

MAX_SOURCE = 100_000
MAX_OUTPUT = 200_000
RUN_TIMEOUT = float(os.environ.get("SEEMS_RUN_TIMEOUT", "60"))
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1", *filter(None, os.environ.get("SEEMS_ALLOWED_HOSTS", "").split(","))}

EXAMPLES = [
    ("01_is_vs_seems", "is vs seems", "Exact conditions run in code. Judgments go to Jev. `unsure:` catches the rest."),
    ("02_support_triage", "Support triage", "kind, scale and judgment blocks route an inbox. Eight tickets, one round trip each."),
    ("03_unsure", "Handling unsure", "The unsure branch, the Unsure exception, and raising the bar with certainty()."),
    ("04_relations", "Comparing two values", "contradicts, answers, means: check draft replies against a policy."),
    ("05_libraries", "With pip libraries", "feedparser reads a feed, Jev judges it, sqlite3 stores and queries the result."),
    ("06_more_python", "More Python", "Judgments inside classes, cached properties, generators, sorting and match."),
]

seems.install()
sys.path.insert(0, APP_DIR)
import desk  # noqa: E402  -- this is desk.seems

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024
app.register_blueprint(desk.bp, url_prefix="/desk")
_runs = threading.BoundedSemaphore(4)


# ---- safety: this server runs code, so only the local browser may talk to it ---- #

def _hostname(value):
    return (urlsplit("//" + value).hostname or "").lower()


@app.before_request
def only_local_pages():
    if _hostname(request.host) not in ALLOWED_HOSTS:
        abort(403, "unknown host")
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("Origin")
        if origin and _hostname(urlsplit(origin).netloc) != _hostname(request.host):
            abort(403, "cross-site request")
        if request.method != "DELETE" and not request.is_json:
            abort(415, "send JSON")


@app.after_request
def headers(response):
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


# ---- pages ------------------------------------------------------------------------ #

@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/desk")
@app.get("/desk/")
def desk_page():
    return send_from_directory(STATIC_DIR, "desk.html")


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(STATIC_DIR, name)


@app.get("/guide")
@app.get("/guide/")
def guide_page():
    return send_from_directory(os.path.join(ROOT, "docs"), "index.html")


@app.get("/guide/<path:name>")
def guide_files(name):
    return send_from_directory(os.path.join(ROOT, "docs"), name)


# ---- api -------------------------------------------------------------------------- #

@app.get("/api/health")
def health():
    return jsonify(ok=True, has_key=bool(api_key_from_env()), model=settings.model,
                   version=seems.__version__)


@app.get("/api/language")
def language():
    return jsonify(describing=list(DESCRIBING), relating=list(RELATING), templates={**DESCRIBING, **RELATING},
                   default_sure=settings.sure, price_per_mtok_usd=PRICE_PER_MTOK_USD)


@app.get("/api/examples")
def examples():
    out = []
    for name, title, blurb in EXAMPLES:
        with open(os.path.join(EXAMPLES_DIR, name + ".seems"), encoding="utf-8") as handle:
            out.append({"id": name, "title": title, "blurb": blurb, "source": handle.read()})
    return jsonify(out)


def _translate(source):
    try:
        result = translate(source, "main.seems")
        compile(result.python, "main.seems", "exec", dont_inherit=True)
    except SyntaxError as err:
        return None, {"line": err.lineno or 1, "col": err.offset or 1,
                      "message": getattr(err, "short", None) or err.msg, "hint": getattr(err, "hint", None)}
    return result, None


@app.post("/api/translate")
def translate_api():
    source = str((request.get_json(silent=True) or {}).get("source") or "")[:MAX_SOURCE]
    result, error = _translate(source)
    if error:
        return jsonify(ok=False, error=error)
    return jsonify(ok=True, python=result.python, changed=result.changed, marks=result.marks)


@app.post("/api/cache/clear")
def clear_cache():
    seems.clear_cache()
    return jsonify(ok=True)


@app.post("/api/run")
def run_api():
    body = request.get_json(silent=True) or {}
    source = str(body.get("source") or "")
    if len(source) > MAX_SOURCE:
        abort(413, "program too long")
    use_cache = bool(body.get("cache", True))
    try:
        sure = min(0.999, max(0.55, float(body.get("sure") or settings.sure)))
    except (TypeError, ValueError):
        sure = settings.sure
    return Response(_run(source, use_cache, sure), mimetype="application/x-ndjson",
                    headers={"X-Accel-Buffering": "no"})


def _line(kind, **fields):
    return json.dumps({"t": kind, **fields}, ensure_ascii=False) + "\n"


def _run(source, use_cache, sure):
    """Run a program in its own process and stream what happens, one JSON object per line."""
    result, error = _translate(source)
    if error:
        yield _line("syntax", **error)
        yield _line("done", code=1, ms=0)
        return
    yield _line("python", code=result.python, changed=result.changed)
    if not _runs.acquire(blocking=False):
        yield _line("err", data="Too many programs are running. Try again in a moment.\n")
        yield _line("done", code=1, ms=0)
        return

    folder = tempfile.mkdtemp(prefix="seems-run-")
    process = None
    try:
        main, trace = os.path.join(folder, "main.seems"), os.path.join(folder, "trace.jsonl")
        out_path, err_path = os.path.join(folder, "out.txt"), os.path.join(folder, "err.txt")
        with open(main, "w", encoding="utf-8") as handle:
            handle.write(source)
        open(trace, "w").close()
        command = [sys.executable, "-m", "seems", "run", "--trace", trace, "--sure", str(sure)]
        if not use_cache:
            command.append("--no-cache")
        command.append(main)
        env = {**os.environ, "PYTHONPATH": ROOT, "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
               "HOME": folder, "TMPDIR": folder}
        started = time.perf_counter()
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            process = subprocess.Popen(command, cwd=EXAMPLES_DIR, env=env, stdin=subprocess.DEVNULL,
                                       stdout=out, stderr=err, start_new_session=True)
        readers = {"out": [open(out_path, "rb"), 0], "err": [open(err_path, "rb"), 0], "trace": [open(trace, "rb"), b""]}
        timed_out = False

        def drain():
            for kind in ("out", "err"):
                handle, sent = readers[kind]
                chunk = handle.read(65536)
                if chunk and sent < MAX_OUTPUT:
                    text = chunk.decode("utf-8", "replace").replace(folder + os.sep, "")
                    readers[kind][1] = sent + len(chunk)
                    yield _line(kind, data=text)
                    if readers[kind][1] >= MAX_OUTPUT:
                        yield _line("err", data="\n[output cut: too long]\n")
            handle, rest = readers["trace"]
            data = rest + handle.read()
            *complete, readers["trace"][1] = data.split(b"\n")
            for raw in complete:
                if raw.strip():
                    yield '{"t": "trace", "event": ' + raw.decode("utf-8", "replace") + "}\n"

        while process.poll() is None:
            yield from drain()
            if time.perf_counter() - started > RUN_TIMEOUT:
                timed_out = True
                _kill(process)
                break
            time.sleep(0.05)
        process.wait(timeout=5)
        yield from drain()
        if timed_out:
            yield _line("err", data=f"\n[stopped after {RUN_TIMEOUT:.0f} seconds]\n")
        yield _line("done", code=process.returncode, ms=round((time.perf_counter() - started) * 1000))
    finally:
        if process is not None and process.poll() is None:
            _kill(process)
        for item in locals().get("readers", {}).values():
            item[0].close()
        shutil.rmtree(folder, ignore_errors=True)
        _runs.release()


def _kill(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "3000")), threaded=True)
