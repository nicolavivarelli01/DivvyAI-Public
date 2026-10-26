import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from openai.types.chat import ChatCompletion
from strands import Agent
from strands.models.openai import Client as OpenAIClient
from strands.models.openai import OpenAIModel

import agent


@pytest.fixture
def offline_server(tmp_path: Path) -> Path:
    path = tmp_path / "offline_server.py"
    path.write_text(
        """from mcp.server import MCPServer
server = MCPServer[None]("Offline fixture")

@server.tool(structured_output=True)
def rank_stations() -> dict[str, object]:
    return {"station_id": "offline-s1", "zero_samples": 3, "valid_samples": 4}

server.run(transport="stdio")
"""
    )
    return path


def completion(tool_id: str | None) -> ChatCompletion:
    message: dict[str, object] = {"role": "assistant"}
    if tool_id is None:
        message["content"] = "offline-s1 was empty in 3 of 4 samples (75%)."
    else:
        message["content"] = None
        message["tool_calls"] = [
            {
                "id": tool_id,
                "type": "function",
                "function": {
                    "name": "rank_stations",
                    "arguments": "{}",
                },
            }
        ]
    return ChatCompletion.model_validate(
        {
            "id": "offline-response",
            "object": "chat.completion",
            "created": 0,
            "model": agent.MODEL_ID,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls" if tool_id else "stop",
                    "message": message,
                }
            ],
        }
    )


def offline_model(responses: list[ChatCompletion]) -> tuple[OpenAIModel, AsyncMock]:
    create = AsyncMock(side_effect=responses)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return OpenAIModel(
        client=cast(OpenAIClient, client),
        model_id=agent.MODEL_ID,
        stream=False,
    ), create


def test_missing_api_key_fails_before_starting_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    make_client = Mock()
    monkeypatch.setattr(agent, "make_client", make_client)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        agent.ask("Which stations were empty?")
    make_client.assert_not_called()


def test_provider_settings_and_child_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-offline-key")
    monkeypatch.setenv("GOOGLE_CLOUD_QUOTA_PROJECT", "divvy-ai")
    model = agent.configured_model()
    assert model.client_args["max_retries"] == 0
    assert model.config["params"] == {
        "max_completion_tokens": 800,
        "reasoning_effort": "none",
        "parallel_tool_calls": False,
    }
    environment = agent.server_parameters().env
    assert environment is not None
    assert "OPENAI_API_KEY" not in environment
    assert environment["GOOGLE_CLOUD_QUOTA_PROJECT"] == "divvy-ai"


def test_model_tool_result_answer_loop_and_cleanup(
    offline_server: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model, create = offline_model([completion("call-1"), completion(None)])
    model.client_args["api_key"] = "synthetic-offline-key"
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-offline-key")
    client = agent.make_client(offline_server)
    remove = Mock(wraps=client.remove_consumer)
    monkeypatch.setattr(client, "remove_consumer", remove)
    monkeypatch.setattr(agent, "make_client", lambda server_path: client)
    instances: list[Agent] = []

    def tracked_agent(**kwargs: Any) -> Agent:
        instance = Agent(**kwargs)
        instances.append(instance)
        return instance

    # Keep the instance alive so this tests explicit cleanup, before __del__.
    monkeypatch.setattr(agent, "Agent", tracked_agent)
    report_path = tmp_path / "reports" / "success.json"
    answer = agent.ask(
        "Rank the observed empty stations.",
        model=model,
        server_path=offline_server,
        report_path=report_path,
    )
    assert "3 of 4 samples (75%)" in answer
    assert create.call_count == 2
    assert "offline-s1" in str(create.call_args.kwargs["messages"])
    assert "TOOL rank_stations" in capsys.readouterr().err
    remove.assert_called_once()
    report = json.loads(report_path.read_text())
    assert report["answer"] == answer
    assert report["model_id"] == agent.MODEL_ID
    assert report["model_calls"] == 2 and report["admitted_tool_calls"] == 1
    assert report["token_usage_reported"]["inputTokens"] == 20
    assert report["token_usage_reported"]["outputTokens"] == 10
    tool = report["tool_requests"][0]
    assert tool["name"] == "rank_stations" and not tool["cancelled"]
    assert tool["result"]["structuredContent"]["station_id"] == "offline-s1"
    assert "synthetic-offline-key" not in report_path.read_text()


def test_repeated_tool_requests_hit_both_limits(
    offline_server: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    model, create = offline_model([completion(f"call-{i}") for i in range(3)])
    report_path = tmp_path / "guard.json"
    answer = agent.ask(
        "Keep requesting the tool.",
        model=model,
        server_path=offline_server,
        report_path=report_path,
    )
    assert create.call_count == agent.MAX_MODEL_CALLS
    assert capsys.readouterr().err.count("TOOL rank_stations") == agent.MAX_TOOL_CALLS
    assert answer == "Stopped: model-call limit reached; answer incomplete."
    report = json.loads(report_path.read_text())
    assert report["model_limit_reached"] and report["tool_limit_reached"]
    assert report["admitted_tool_calls"] == 2
    assert len(report["tool_requests"]) == 3
    assert report["tool_requests"][-1]["cancelled"]
    assert report["tool_requests"][-1]["duration_seconds"] is None


def test_existing_report_is_preserved_before_any_model_request(tmp_path: Path) -> None:
    path = tmp_path / "existing.json"
    path.write_text('{"saved": true}')
    model, create = offline_model([])
    with pytest.raises(ValueError, match="already exists"):
        agent.ask("Question", model=model, report_path=path)
    create.assert_not_called()
    assert path.read_text() == '{"saved": true}'
