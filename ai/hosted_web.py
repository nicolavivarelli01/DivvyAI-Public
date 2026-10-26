"""Agent-only HTTP API; Cloud Run IAM must protect the deployed service."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from local_web import (
    MAX_BODY_BYTES,
    RESPONSE_TIMEOUT_SECONDS,
    AgentUnavailableError,
    Question,
    Runner,
    RunningQuestion,
    error_response,
    run_question,
)


def create_app(
    *,
    runner: Runner = run_question,
    response_timeout: float = RESPONSE_TIMEOUT_SECONDS,
) -> Starlette:
    if not 0 < response_timeout <= RESPONSE_TIMEOUT_SECONDS:
        raise ValueError("Use a response timeout between zero and 100 seconds.")
    running = RunningQuestion()

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        yield
        if running.active is not None:
            try:
                await asyncio.shield(running.active)
            except Exception:
                pass

    async def response_headers(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request.state.request_id = uuid4().hex
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

    async def answer(request: Request) -> Response:
        request_id = str(request.state.request_id)
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
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
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    return error_response(
                        request_id,
                        413,
                        "INVALID_QUESTION",
                        "The request body is too large.",
                    )
                body.extend(chunk)
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
                "No answer arrived in time. Work may still be running. "
                "No automatic retry was made.",
            )
        except AgentUnavailableError:
            return error_response(
                request_id,
                503,
                "BACKEND_UNAVAILABLE",
                "The agent is not configured. Contact the service owner.",
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
            Route("/health", health),
            Route("/api/agent", answer, methods=["POST"]),
        ],
        middleware=[Middleware(BaseHTTPMiddleware, dispatch=response_headers)],
        lifespan=lifespan,
    )
