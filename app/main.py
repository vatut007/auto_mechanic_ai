"""auto_mechanic_ai core — FastAPI exposing an OpenAI-compatible API.

Open WebUI (and any OpenAI-speaking client) connects here:
  GET  /v1/models              — provider models as public aliases
  POST /v1/chat/completions    — chat with SSE streaming support
  GET  /healthz                — liveness + registry/key overview
"""
from __future__ import annotations

import json
import os
import time
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .agent import build_messages
from .config import Registry, db_path, load_registry
from .provider import OpenAICompatProvider, ProviderError
from .usage_log import UsageLog

registry: Registry = load_registry()
usage_log = UsageLog(db_path())

API_KEYS = [k.strip() for k in os.getenv("AMAI_API_KEYS", "").split(",") if k.strip()]

app = FastAPI(title="auto_mechanic_ai core", version="0.1.0")

FORWARD_PARAMS = (
    "temperature", "top_p", "max_tokens", "tools", "tool_choice",
    "stop", "presence_penalty", "frequency_penalty", "seed", "response_format",
)


def _openai_error(status: int, message: str, err_type: str = "provider_error") -> dict:
    return {"error": {"message": message, "type": err_type, "param": None, "code": status}}


def _authorized(request: Request) -> bool:
    if not API_KEYS:
        return True
    auth = request.headers.get("authorization", "")
    return auth.startswith("Bearer ") and auth[7:] in API_KEYS


@app.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "auth_required": bool(API_KEYS),
        "providers": {
            name: {
                "models": sorted(p.models),
                "key_configured": p.has_key(),
            }
            for name, p in registry.providers.items()
        },
        "embeddings": registry.embeddings,
    }


@app.get("/v1/models")
def list_models(request: Request):
    if not _authorized(request):
        return JSONResponse(status_code=401, content=_openai_error(401, "invalid api key", "auth_error"))
    return {
        "object": "list",
        "data": [
            {
                "id": alias,
                "object": "model",
                "owned_by": pname,
                "context_tokens": mspec.context_tokens,
                "capabilities": mspec.capabilities,
                "key_configured": registry.providers[pname].has_key(),
            }
            for alias, (pname, mspec) in sorted(registry.aliases.items())
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    if not _authorized(request):
        return JSONResponse(status_code=401, content=_openai_error(401, "invalid api key", "auth_error"))

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_openai_error(400, "body must be JSON", "invalid_request_error"))

    alias = body.get("model")
    if not alias:
        return JSONResponse(status_code=400, content=_openai_error(400, "model is required", "invalid_request_error"))
    if alias not in registry.aliases:
        known = ", ".join(sorted(registry.aliases))
        return JSONResponse(
            status_code=404,
            content=_openai_error(404, f"unknown model {alias!r}; available: {known}", "invalid_request_error"),
        )

    try:
        messages = build_messages(body.get("messages") or [])
    except ValueError as e:
        return JSONResponse(status_code=400, content=_openai_error(400, str(e), "invalid_request_error"))

    pspec, mspec = registry.resolve(alias)
    payload: dict = {"model": mspec.id, "messages": messages}
    for key in FORWARD_PARAMS:
        if body.get(key) is not None:
            payload[key] = body[key]

    provider = OpenAICompatProvider(pspec)
    started = time.monotonic()
    stream = bool(body.get("stream"))

    if stream:
        return StreamingResponse(
            _stream_response(provider, payload, alias, pspec.name, mspec.id, started),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    try:
        result = await provider.complete(payload)
    except ProviderError as e:
        usage_log.log(alias=alias, provider=pspec.name, model=mspec.id, usage=None,
                      stream=False, duration_ms=_ms(started), status="error", error=e.message)
        return JSONResponse(status_code=e.status, content=_openai_error(e.status, e.message, e.err_type))

    result["model"] = alias  # surface the public alias back to the client
    usage_log.log(alias=alias, provider=pspec.name, model=mspec.id, usage=result.get("usage"),
                  stream=False, duration_ms=_ms(started))
    return JSONResponse(result)


async def _stream_response(
    provider: OpenAICompatProvider,
    payload: dict,
    alias: str,
    pname: str,
    model_id: str,
    started: float,
) -> AsyncIterator[str]:
    """Relay provider SSE chunks, tagging them with the public alias."""
    usage: dict | None = None
    error: str | None = None
    try:
        async for chunk in provider.complete_stream(payload):
            if chunk.get("usage"):
                usage = chunk["usage"]
            if chunk.get("model"):
                chunk["model"] = alias
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    except ProviderError as e:
        error = e.message
        yield f"data: {json.dumps(_openai_error(e.status, e.message, e.err_type), ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    finally:
        usage_log.log(alias=alias, provider=pname, model=model_id, usage=usage,
                      stream=True, duration_ms=_ms(started),
                      status="error" if error else "ok", error=error)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
