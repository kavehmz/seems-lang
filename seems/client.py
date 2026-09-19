"""HTTP client for the TypeSafe System One endpoint. Standard library only."""
from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request

API_URL = "https://api.typesafe.ai/v1/systemone"
RETRY_STATUSES = {408, 409, 429, 500, 502, 503, 504, 529}


class JevError(RuntimeError):
    """The TypeSafe API could not answer."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def api_key_from_env():
    return os.environ.get("TYPESAFE_API_KEY") or os.environ.get("TYPESAFE_API") or ""


class JevClient:
    def __init__(self, api_key=None, url=None, timeout=30.0, attempts=4):
        self.api_key = api_key if api_key is not None else api_key_from_env()
        self.url = url or os.environ.get("TYPESAFE_URL") or API_URL
        self.timeout = timeout
        self.attempts = attempts

    def ask(self, state, questions: dict, model: str) -> dict:
        """One request: one state, many typed questions."""
        if not self.api_key:
            raise JevError("No TypeSafe API key. Set TYPESAFE_API_KEY (or TYPESAFE_API) in the environment")
        body = json.dumps({"state": state, "model": model, "questions": questions},
                          ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "seems-lang/0.1",
        })
        last = None
        for attempt in range(self.attempts):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as err:
                detail = err.read().decode("utf-8", "replace")[:400]
                last = JevError(f"TypeSafe answered HTTP {err.code}: {detail}", err.code)
                if err.code not in RETRY_STATUSES:
                    raise last from None
                wait = _retry_after(err.headers.get("retry-after"))
            except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
                last = JevError(f"Could not reach TypeSafe: {getattr(err, 'reason', err)}")
                wait = None
            if attempt < self.attempts - 1:
                time.sleep(wait if wait is not None else min(8.0, 0.5 * 2 ** attempt) + random.random() * 0.25)
        raise last


def _retry_after(value):
    try:
        return max(0.0, min(30.0, float(value)))
    except (TypeError, ValueError):
        return None
