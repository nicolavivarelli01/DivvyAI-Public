import threading
from collections.abc import Iterator
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient

import hosted_web
from local_web import MAX_BODY_BYTES, AgentUnavailableError


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Mock]]:
    monkeypatch.setenv("K_SERVICE", "synthetic-hosted-service")
    runner = Mock(return_value={"answer": "Synthetic answer.", "tool_requests": []})
    app = hosted_web.create_app(runner=runner)
    with TestClient(app) as http:
        yield http, runner


def test_health_and_unknown_routes_do_not_run_agent(
    client: tuple[TestClient, Mock],
) -> None:
    http, runner = client
    assert http.get("/health").json() == {"status": "ok", "busy": False}
    assert http.get("/model-testing.html").status_code == 404
    assert http.get("/api/agent").status_code == 405
    runner.assert_not_called()


def test_question_runs_once_and_preserves_report(
    client: tuple[TestClient, Mock],
) -> None:
    http, runner = client
    response = http.post("/api/agent", json={"question": "  One question.  "})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    result = response.json()
    assert result["request_id"] and result["status"] == "ok"
    assert result["answer"] == "Synthetic answer."
    assert result["tool_requests"] == []
    runner.assert_called_once_with("One question.")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": " "},
        {"question": 12},
        {"question": "x" * 4001},
        {"question": "Question", "extra": True},
    ],
)
def test_invalid_questions_do_not_run_agent(
    client: tuple[TestClient, Mock], payload: dict[str, object]
) -> None:
    http, runner = client
    response = http.post("/api/agent", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_QUESTION"
    runner.assert_not_called()


def test_wrong_content_type_malformed_json_and_large_body_are_rejected(
    client: tuple[TestClient, Mock],
) -> None:
    http, runner = client
    assert http.post("/api/agent", content="text").status_code == 400
    headers = {"Content-Type": "application/json"}
    assert http.post("/api/agent", content="{", headers=headers).status_code == 422
    response = http.post(
        "/api/agent",
        content=b" " * (MAX_BODY_BYTES + 1),
        headers=headers,
    )
    assert response.status_code == 413
    runner.assert_not_called()


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (AgentUnavailableError("synthetic-private-key"), 503),
        (RuntimeError("synthetic-private-provider-detail"), 502),
    ],
)
def test_worker_failures_are_sanitized_without_retry(
    client: tuple[TestClient, Mock], failure: Exception, expected_status: int
) -> None:
    http, runner = client
    runner.side_effect = failure
    response = http.post("/api/agent", json={"question": "Question"})
    assert response.status_code == expected_status
    assert str(failure) not in response.text
    assert response.headers["cache-control"] == "no-store"
    runner.assert_called_once()


def test_timeout_keeps_slot_until_worker_finishes() -> None:
    entered = threading.Event()
    release = threading.Event()

    def slow(question: str) -> dict[str, object]:
        entered.set()
        assert release.wait(3)
        return {"answer": "Synthetic answer."}

    runner = Mock(side_effect=slow)
    app = hosted_web.create_app(runner=runner, response_timeout=0.03)
    with TestClient(app) as http:
        try:
            response = http.post("/api/agent", json={"question": "First"})
            assert entered.is_set()
            assert response.status_code == 504
            assert http.get("/health").json()["busy"] is True
            second = http.post("/api/agent", json={"question": "Second"})
            assert second.status_code == 429
            assert second.headers["retry-after"] == "10"
            runner.assert_called_once_with("First")
        finally:
            release.set()


@pytest.mark.parametrize("timeout", [0.0, -1.0, 101.0, float("nan")])
def test_invalid_timeout_is_rejected(timeout: float) -> None:
    with pytest.raises(ValueError, match="response timeout"):
        hosted_web.create_app(response_timeout=timeout)
