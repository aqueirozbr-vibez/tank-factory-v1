"""Unit 53: the committed OpenAPI document matches the application and types every read route."""

from orchestrator.api.openapi import DOCUMENT, check_document, document, documented_app

READ_ROUTES = (
    "/api/v1/installation",
    "/api/v1/tasks",
    "/api/v1/tasks/{task_id}",
    "/api/v1/tasks/{task_id}/events",
    "/api/v1/tasks/{task_id}/events/stream",
    "/api/v1/tasks/{task_id}/envelopes",
    "/api/v1/tasks/{task_id}/gates",
    "/api/v1/tasks/{task_id}/review",
    "/api/v1/approvals/plans",
    "/api/v1/approvals/questions",
    "/api/v1/audit/queue",
    "/api/v1/kpis",
    "/api/v1/indicators",
    "/api/v1/needs-human/count",
    "/api/v1/eval/sets",
    "/api/v1/eval/runs",
    "/api/v1/eval/runs/{run_id}",
    "/api/v1/config",
    "/api/v1/config/history",
    "/api/v1/changes",
)


def test_committed_document_matches_the_application() -> None:
    assert DOCUMENT.is_file(), "run `factory openapi` and commit openapi.json"
    assert check_document(DOCUMENT), "openapi.json is stale: run `factory openapi` and commit it"


def test_every_read_route_is_documented_with_its_model() -> None:
    doc = document(documented_app())
    paths = doc["paths"]
    assert isinstance(paths, dict)
    for route in READ_ROUTES:
        assert route in paths, route
        operation = paths[route]["get"]
        assert "200" in operation["responses"] and "403" in operation["responses"]
    tasks = paths["/api/v1/tasks"]["get"]["responses"]["200"]["content"]["application/json"]
    assert tasks["schema"]["$ref"].endswith("/TaskPage")
    stream = paths["/api/v1/tasks/{task_id}/events/stream"]["get"]["responses"]["200"]
    assert "text/event-stream" in stream["content"]
    info = doc["info"]
    assert isinstance(info, dict) and info["version"] == "v1"
    schemas = doc["components"]["schemas"]  # type: ignore[index]
    assert {"ErrorBody", "TaskDetail", "EventPage", "KpiResponse"} <= set(schemas)
