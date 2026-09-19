"""Run every example against the real TypeSafe API and report what happened.

    docker compose run --rm app python tools/live_check.py

Needs the API key from .env. Every run asks Jev again (no cache).
"""
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")


def main():
    failures = 0
    print(f"{'example':<26}{'exit':>5}{'judgments':>11}{'unsure':>8}{'requests':>10}{'tokens':>8}{'cost usd':>11}{'seconds':>9}")
    for name in sorted(f for f in os.listdir(EXAMPLES) if f.endswith(".seems")):
        with tempfile.NamedTemporaryFile("r", suffix=".jsonl") as trace:
            started = time.perf_counter()
            done = subprocess.run(
                [sys.executable, "-m", "seems", "run", "--no-cache", "--trace", trace.name, name],
                cwd=EXAMPLES, env={**os.environ, "PYTHONPATH": ROOT}, capture_output=True, text=True, timeout=180)
            seconds = time.perf_counter() - started
            events = [json.loads(line) for line in trace.read().splitlines() if line.strip()]
        judgments = [e for e in events if e["type"] == "judgment"]
        requests = [e for e in events if e["type"] == "request"]
        tokens = sum(r.get("input_tokens", 0) for r in requests)
        unsure = sum(1 for j in judgments if j.get("verdict") == "unsure")
        print(f"{name[:-6]:<26}{done.returncode:>5}{len(judgments):>11}{unsure:>8}{len(requests):>10}{tokens:>8}"
              f"{tokens * 0.042 / 1e6:>11.6f}{seconds:>9.2f}")
        errors = [e for e in events if e.get("error")]
        if done.returncode != 0 or errors:
            failures += 1
            print(done.stderr.strip() or errors[0]["error"])
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
