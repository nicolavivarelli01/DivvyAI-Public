"""Serve the existing website and agent together on this Mac, over loopback only."""

import argparse
import asyncio
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from uuid import uuid4

import uvicorn
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

import agent

MAX_BODY_BYTES = 65536
RESPONSE_TIMEOUT_SECONDS = 100.0
Runner = Callable[[str], dict[str, object]]


class Question(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True, extra="forbid")
    question: str = Field(min_length=1, max_length=4000)


class ToolTrace(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    name: str = Field(min_length=1)
    arguments: dict[str, object]
    result: dict[str, object]
    cancelled: bool
    duration_seconds: float | None = None
    error_type: str | None = None


class AgentReport(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
    answer: str = Field(min_length=1)
    model_id: str
    started_at_utc: AwareDatetime
    finished_at_utc: AwareDatetime
    model_limit_reached: bool
    tool_limit_reached: bool
    tool_requests: list[ToolTrace]
    token_usage_reported: dict[str, int | float]


class AgentUnavailableError(Exception):
    """The local model credential has not been configured."""


def run_question(question: str) -> dict[str, object]:
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise AgentUnavailableError
    # ask() already records real MCP results, flags and usage. Reuse that path.
    with TemporaryDirectory(prefix="divvyai-web-") as directory:
        report_path = Path(directory) / "report.json"
        agent.ask(question, report_path=report_path)
        if report_path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Agent report exceeds the local response limit.")
        report = AgentReport.model_validate_json(report_path.read_bytes(), strict=True)
        return report.model_dump(mode="json")


class WebsiteFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        parts = PurePosixPath(path).parts
        root_page = path in {"index.html", "model-testing.html"}
        asset = (
            len(parts) > 1
            and parts[0] in {"assets", "css", "js"}
            and PurePosixPath(path).suffix.lower()
            in {".css", ".js", ".svg", ".webp", ".png", ".jpg", ".jpeg", ".ico"}
        )
        if any(part.startswith(".") for part in parts) or not (root_page or asset):
            raise HTTPException(404)
        # StaticFiles resolves paths and rejects symlinks outside directory.
        return await super().get_response(path, scope)


@dataclass
class RunningQuestion:
    active: asyncio.Task[dict[str, object]] | None = None


def error_response(
    request_id: str,
    status: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        {"request_id": request_id, "error": {"code": code, "message": message}},
        status_code=status,
        headers=headers,
    )


def create_app(
    website_dir: Path,
    *,
    port: int = 8000,
    runner: Runner = run_question,
    response_timeout: float = RESPONSE_TIMEOUT_SECONDS,
) -> Starlette:
    if os.environ.get("K_SERVICE"):
        raise ValueError(
            "This entry point is local only; cloud access is not configured."
        )
    if not 1 <= port <= 65535 or not 0 < response_timeout <= RESPONSE_TIMEOUT_SECONDS:
        raise ValueError("Use a valid local port and response timeout.")
    website_dir = website_dir.resolve()
    if not (website_dir / "model-testing.html").is_file():
        raise ValueError("website-dir must contain Claude's model-testing.html page.")
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{host}" for host in allowed_hosts}
    running = RunningQuestion()

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        yield
        # Let the existing agent's finally/cleanup finish before normal shutdown.
        if running.active is not None:
            try:
                await asyncio.shield(running.active)
            except Exception:
                pass

    async def local_guard(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request.state.request_id = uuid4().hex
        origin = request.headers.get("origin")
        if request.headers.get("host") not in allowed_hosts or (
            origin is not None and origin not in allowed_origins
        ):
            response: Response = error_response(
                request.state.request_id,
                403,
                "LOCAL_ONLY",
                "Open this page through the local DivvyAI server.",
            )
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def health(request: Request) -> Response:
        return JSONResponse(
            {
                "status": "ok",
                "busy": running.active is not None and not running.active.done(),
            }
        )

    async def home(request: Request) -> Response:
        return RedirectResponse("/model-testing.html")

    async def answer(request: Request) -> Response:
        request_id = str(request.state.request_id)
        if (
            request.headers.get("content-type", "").split(";", 1)[0].lower()
            != "application/json"
        ):
            return error_response(
                request_id,
                400,
                "INVALID_QUESTION",
                "Send a JSON object with one question.",
            )
        body = bytearray()
        try:
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    return error_response(
                        request_id,
                        413,
                        "INVALID_QUESTION",
                        "The request body is too large.",
                    )
            question = Question.model_validate_json(body).question
        except (ValidationError, ClientDisconnect):
            return error_response(
                request_id,
                422,
                "INVALID_QUESTION",
                "Send one nonblank question of at most 4,000 characters.",
            )

        if running.active is not None and not running.active.done():
            return error_response(
                request_id,
                429,
                "BUSY",
                "The previous question is still running. Wait before submitting another.",
                headers={"Retry-After": "10"},
            )
        task = asyncio.create_task(asyncio.to_thread(runner, question))
        running.active = task
        # Consume late errors without printing provider errors or secrets.
        task.add_done_callback(
            lambda done: None if done.cancelled() else done.exception()
        )
        try:
            report = await asyncio.wait_for(asyncio.shield(task), response_timeout)
            return JSONResponse({**report, "status": "ok", "request_id": request_id})
        except TimeoutError:
            return error_response(
                request_id,
                504,
                "TIMEOUT",
                "No answer arrived in time. The request may still finish; it was not "
                "retried. Wait for it to finish before submitting another question.",
            )
        except AgentUnavailableError:
            return error_response(
                request_id,
                503,
                "BACKEND_UNAVAILABLE",
                "Set OPENAI_API_KEY privately in the terminal that starts this server.",
            )
        except Exception:
            return error_response(
                request_id,
                502,
                "AGENT_FAILED",
                "The agent could not return a valid answer. No automatic retry was made.",
            )

    return Starlette(
        routes=[
            Route("/", home),
            Route("/health", health),
            Route("/api/agent", answer, methods=["POST"]),
            Mount("/", app=WebsiteFiles(directory=website_dir)),
        ],
        middleware=[Middleware(BaseHTTPMiddleware, dispatch=local_guard)],
        lifespan=lifespan,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--website-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    try:
        app = create_app(args.website_dir, port=args.port)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(f"Open http://127.0.0.1:{args.port}/model-testing.html", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, proxy_headers=False)
