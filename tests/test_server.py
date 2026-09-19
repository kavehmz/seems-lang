"""The demo server: pages, translate, run stream and the local-only guard."""
import json

import pytest

flask = pytest.importorskip("flask")


@pytest.fixture
def client(jev, monkeypatch, tmp_path):
    monkeypatch.setenv("DESK_DB", str(tmp_path / "desk.sqlite"))
    from app import server
    server.desk.DB_PATH = str(tmp_path / "desk.sqlite")
    return server.app.test_client()


def post(client, url, body, **headers):
    return client.post(url, data=json.dumps(body), content_type="application/json", headers=headers)


def test_pages_and_examples(client):
    assert client.get("/").status_code == 200
    assert client.get("/desk").status_code == 200
    assert client.get("/api/health").get_json()["ok"] is True
    examples = client.get("/api/examples").get_json()
    assert len(examples) == 6 and all("source" in e for e in examples)


def test_every_example_translates(client):
    for example in client.get("/api/examples").get_json():
        reply = post(client, "/api/translate", {"source": example["source"]}).get_json()
        assert reply["ok"], (example["id"], reply)
        assert reply["marks"]


def test_translate_reports_the_line_of_a_mistake(client):
    reply = post(client, "/api/translate", {"source": "x = 1\nunsure:\n    pass\n"}).get_json()
    assert reply["ok"] is False and reply["error"]["line"] == 2


def test_only_local_json_requests_are_accepted(client):
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 403
    assert post(client, "/api/run", {"source": ""}, Origin="http://evil.example").status_code == 403
    assert client.post("/api/run", data={"source": "x"}).status_code == 415


def test_run_streams_output_and_exit_code(client):
    source = "import sys\nprint('hello')\nprint('oops', file=sys.stderr)\nraise SystemExit(3)\n"
    lines = [json.loads(line) for line in post(client, "/api/run", {"source": source}).data.splitlines()]
    kinds = [m["t"] for m in lines]
    assert kinds[0] == "python" and kinds[-1] == "done"
    assert {"t": "out", "data": "hello\n"} in lines
    assert any(m["t"] == "err" and "oops" in m["data"] for m in lines)
    assert lines[-1]["code"] == 3


def test_run_reports_syntax_errors_without_starting_a_process(client):
    lines = [json.loads(line) for line in post(client, "/api/run", {"source": "if x seems:\n"}).data.splitlines()]
    assert lines[0]["t"] == "syntax" and lines[-1] == {"t": "done", "code": 1, "ms": 0}


def test_the_desk_is_a_seems_module_and_routes_tickets(client, jev):
    from app import server
    assert server.desk.__file__.endswith("desk.seems")

    reply = post(client, "/desk/api/tickets", {"text": "billing: please refund my money back", "amount": 900})
    assert reply.status_code == 201
    ticket = reply.get_json()
    assert ticket["queue"] == "manager" and ticket["team"] == "billing"
    assert len(jev.requests) == 1  # every judgment of the ticket in one request

    ticket = post(client, "/desk/api/tickets", {"text": "technical: total outage, I am furious", "amount": 0}).get_json()
    assert ticket["queue"] == "priority"
    ticket = post(client, "/desk/api/tickets", {"text": "hmm", "amount": 900}).get_json()
    assert ticket["queue"] == "human review"

    assert len(client.get("/desk/api/tickets").get_json()) == 3
    assert post(client, "/desk/api/tickets", {"text": ""}).status_code == 400
    assert client.delete("/desk/api/tickets").status_code == 200
    assert client.get("/desk/api/tickets").get_json() == []
    assert "kind team" in client.get("/desk/api/source").get_json()["source"]
