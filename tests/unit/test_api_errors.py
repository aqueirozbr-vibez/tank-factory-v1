"""Routing errors take the error body; a denied control on an unknown task links no task."""

import json
from urllib.parse import quote

from orchestrator.trace import TraceWriter
from tests.unit.test_controls import client_for, headers

UNKNOWN = "acme/atlas-api#9"


def test_routing_errors_take_the_error_body(trace_writer: TraceWriter) -> None:
    client, _ = client_for(trace_writer)
    nowhere = client.get("/api/v1/nothing-here")
    assert nowhere.status_code == 404
    assert nowhere.json()["code"] == "route" and nowhere.json()["traceId"]
    odd = client.get("/api/v1/tasks/a%0Ab")  # a newline in the path parameter matches no route
    assert odd.status_code == 404 and odd.json()["code"] == "route"
    options = client.options("/api/v1/eval/runs")  # GET in the read router, POST in the control one
    assert options.status_code == 405 and options.json()["code"] == "method"
    assert options.headers["allow"] == "GET, POST"
    put = client.put("/api/v1/tasks")
    assert put.status_code == 405 and put.headers["allow"] == "GET"
    audit = client.options("/api/v1/audit/queue")  # /audit/{task_id} matches too; not this resource
    assert audit.status_code == 405 and audit.headers["allow"] == "GET"


def test_a_denied_control_on_an_unknown_task_links_no_task(trace_writer: TraceWriter) -> None:
    w = trace_writer
    client, _ = client_for(w)
    path = f"/api/v1/tasks/{quote(UNKNOWN, safe='')}"
    export = client.get(f"{path}/export", headers=headers("reader"))
    assert export.status_code == 403 and export.json()["code"] == "export_denied"
    budget = client.post(f"{path}/budget", headers=headers("reader"), json={"budget_usd": 7})
    assert budget.status_code == 403
    rows = w.db.fetchall(
        "SELECT task_id, payload_json FROM events WHERE kind = 'control' ORDER BY seq"
    )
    assert [row["task_id"] for row in rows] == [None, None]
    assert [json.loads(row["payload_json"])["task_id"] for row in rows] == [UNKNOWN, UNKNOWN]
    assert [json.loads(row["payload_json"])["attempted"] for row in rows] == [
        "export",
        "budget_raise",
    ]
