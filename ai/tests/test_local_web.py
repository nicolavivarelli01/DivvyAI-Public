import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient

import agent
import local_web

ORIGIN = "http://127.0.0.1:8000"


@pytest.fixture
def website(tmp_path: Path) -> Path:
    site = tmp_path / "website"
    site.mkdir()
    (site / "model-testing.html").write_text("<h1>Model testing</h1>")
    (site / "index.html").write_text("<h1>Homepage</h1>")
    (site / "js").mkdir()
    (site / "js" / "agent-client.js").write_text("// synthetic static file")
    return site


@pytest.fixture
def report() -> dict[str, object]:
    return {
        "answer": "Synthetic: 1.25 bikes from four samples; docks are unknown.",
        "model_id": "offline-model",
        "started_at_utc": "2026-10-25T10:00:00Z",
        "finished_at_utc": "2026-10-25T10:00:01Z",
        "model_limit_reached": False,
        "tool_limit_reached": False,
        "token_usage_reported": {"inputTokens": 20, "outputTokens": 10},
        "tool_requests": [
            {
                "name": "get_station_availability",
                "arguments": {"station_id": "offline-s1"},
                "cancelled": False,
                "duration_seconds": 0.1,
                "error_type": None,
                "result": {
                    "status": "success",
                    "structuredContent": {
                        "hours": [
                            {"avg_bikes_available": 1.25, "avg_docks_available": None}
                        ],
                        "note": "Missing samples are unknown.",
                    },
                },
            }
        ],
    }


@pytest.fixture
def client(
    website: Path, report: dict[str, object]
) -> Iterator[tuple[TestClient, Mock]]:
    runner = Mock(return_value=report)
    app = local_web.create_app(website, runner=runner)
    with TestClient(app, base_url=ORIGIN) as http:
        yield http, runner


def test_static_page_and_health_do_not_run_agent(
    client: tuple[TestClient, Mock],
) -> None:
    http, runner = client
    assert http.get("/model-testing.html").status_code == 200
    assert http.get("/js/agent-client.js").status_code == 200
    assert http.get("/health").json() == {"status": "ok", "busy": False}
    runner.assert_not_called()


def test_question_is_forwarded_once_and_evidence_is_preserved(
    client: tuple[TestClient, Mock], report: dict[str, object]
) -> None:
    http, runner = client
    response = http.post(
        "/api/agent",
        json={"question": "  Show one hour.  "},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["status"] == "ok" and result["request_id"]
    assert result["answer"] == report["answer"]
    assert result["tool_requests"] == report["tool_requests"]
    assert result["token_usage_reported"] == report["token_usage_reported"]
    runner.assert_called_once_with("Show one hour.")


@pytest.mark.parametrize("question", ["", "  ", None, 12, "x" * 4001])
def test_invalid_questions_are_rejected_before_execution(
    client: tuple[TestClient, Mock], question: object
) -> None:
    http, runner = client
    assert http.post("/api/agent", json={"question": question}).status_code == 422
    runner.assert_not_called()


def test_unicode_limit_and_bad_bodies(client: tuple[TestClient, Mock]) -> None:
    http, runner = client
    assert http.post("/api/agent", json={"question": "🚲" * 4000}).status_code == 200
    runner.assert_called_once()
    runner.reset_mock()
    assert http.post("/api/agent", content="not json").status_code == 400
    assert (
        http.post(
            "/api/agent", content="{", headers={"Content-Type": "application/json"}
        ).status_code
        == 422
    )
    assert (
        http.post(
            "/api/agent",
            content=b" " * (local_web.MAX_BODY_BYTES + 1),
            headers={"Content-Type": "application/json"},
        ).status_code
        == 413
    )
    runner.assert_not_called()


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.example:8000"},
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
    ],
)
def test_other_websites_cannot_invoke_local_agent(
    client: tuple[TestClient, Mock], headers: dict[str, str]
) -> None:
    http, runner = client
    response = http.post("/api/agent", json={"question": "Question"}, headers=headers)
    assert response.status_code == 403
    runner.assert_not_called()


def test_private_files_and_outside_symlinks_are_not_served(
    client: tuple[TestClient, Mock], website: Path
) -> None:
    http, _ = client
    (website / "private.txt").write_text("Synthetic private fixture")
    outside = website.parent / "outside.js"
    outside.write_text("Synthetic outside fixture")
    (website / "js" / "outside.js").symlink_to(outside)
    assert http.get("/private.txt").status_code == 404
    assert http.get("/js/outside.js").status_code == 404
    assert http.get("/js/%2e%2e/private.txt").status_code == 404


def test_worker_errors_are_sanitized(client: tuple[TestClient, Mock]) -> None:
    http, runner = client
    runner.side_effect = RuntimeError("synthetic-private-provider-detail")
    response = http.post("/api/agent", json={"question": "Question"})
    assert response.status_code == 502
    assert "synthetic-private-provider-detail" not in response.text
    runner.side_effect = local_web.AgentUnavailableError()
    assert http.post("/api/agent", json={"question": "Question"}).status_code == 503


def test_timeout_keeps_the_slot_until_work_finishes(
    website: Path, report: dict[str, object]
) -> None:
    release = threading.Event()
    entered = threading.Event()

    def slow(question: str) -> dict[str, object]:
        entered.set()
        assert release.wait(3)
        return report

    runner = Mock(side_effect=slow)
    app = local_web.create_app(website, runner=runner, response_timeout=0.03)
    with TestClient(app, base_url=ORIGIN) as http:
        try:
            first = http.post("/api/agent", json={"question": "First"})
            assert entered.is_set()
            assert (
                first.status_code == 504 and first.json()["error"]["code"] == "TIMEOUT"
            )
            assert "may still finish" in first.json()["error"]["message"]
            second = http.post("/api/agent", json={"question": "Second"})
            assert second.status_code == 429 and second.headers["retry-after"] == "10"
            runner.assert_called_once()
        finally:
            release.set()
        deadline = time.monotonic() + 2
        while http.get("/health").json()["busy"] and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        assert not http.get("/health").json()["busy"]
        runner.side_effect = None
        runner.return_value = report
        assert http.post("/api/agent", json={"question": "Third"}).status_code == 200
        assert runner.call_count == 2


def test_existing_report_path_is_reused_then_removed(
    report: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    paths: list[Path] = []

    def fake_ask(question: str, *, report_path: Path) -> str:
        assert not report_path.exists()
        paths.append(report_path)
        report_path.write_text(json.dumps(report))
        return str(report["answer"])

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-offline-key")
    monkeypatch.setattr(agent, "ask", fake_ask)
    result = local_web.run_question("Question")
    assert result["tool_requests"] == report["tool_requests"]
    assert "synthetic-offline-key" not in json.dumps(result)
    assert len(paths) == 1 and not paths[0].parent.exists()


def test_missing_key_and_malformed_report_never_become_answers(
    client: tuple[TestClient, Mock], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, runner = client
    runner.side_effect = local_web.run_question
    make_answer = Mock()
    monkeypatch.setattr(agent, "ask", make_answer)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert http.post("/api/agent", json={"question": "Question"}).status_code == 503
    make_answer.assert_not_called()

    def broken_report(question: str, *, report_path: Path) -> str:
        report_path.write_text('{"answer": "incomplete fixture"}')
        return "incomplete fixture"

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-offline-key")
    monkeypatch.setattr(agent, "ask", broken_report)
    response = http.post("/api/agent", json={"question": "Question"})
    assert response.status_code == 502
    assert "incomplete fixture" not in response.text


def test_cloud_entry_point_is_explicitly_disabled(
    website: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("K_SERVICE", "synthetic-cloud-service")
    with pytest.raises(ValueError, match="local only"):
        local_web.create_app(website)
