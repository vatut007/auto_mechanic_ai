"""Single OpenAI-compatible transport for every provider.

Provider-specific details live in config/providers.yaml; this class only knows
base_url + api key + how to POST /chat/completions (streaming or not).
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .config import ProviderSpec

DEFAULT_TIMEOUT = float(__import__("os").getenv("AMAI_PROVIDER_TIMEOUT", "180"))


class ProviderError(RuntimeError):
    """Provider-side or transport failure, mapped to an OpenAI-style error."""

    def __init__(self, status: int, message: str, err_type: str = "provider_error"):
        super().__init__(message)
        self.status = status
        self.message = message
        self.err_type = err_type


def _error_from_body(body: bytes | str, status: int) -> str:
    try:
        data = json.loads(body)
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            return err.get("message", json.dumps(err)) if isinstance(err, dict) else str(err)
        return json.dumps(data)[:500]
    except Exception:
        return (body if isinstance(body, str) else body.decode("utf-8", "replace"))[:500] or f"HTTP {status}"


class OpenAICompatProvider:
    def __init__(self, spec: ProviderSpec, timeout: float = DEFAULT_TIMEOUT):
        self.spec = spec
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self.spec.api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def complete(self, payload: dict) -> dict:
        """Non-streaming call; returns the provider JSON response."""
        url = f"{self.spec.base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(url, headers=self._headers(), json=payload)
            except httpx.HTTPError as e:
                raise ProviderError(502, f"provider unreachable: {e}") from e
        if resp.status_code >= 400:
            raise ProviderError(resp.status_code, _error_from_body(resp.content, resp.status_code))
        return resp.json()

    async def complete_stream(self, payload: dict) -> AsyncIterator[dict]:
        """Streaming call; yields parsed SSE data payloads (dicts)."""
        url = f"{self.spec.base_url}/chat/completions"
        payload = {**payload, "stream": True, "stream_options": {"include_usage": True}}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                async with client.stream("POST", url, headers=self._headers(), json=payload) as resp:
                    if resp.status_code >= 400:
                        body = await resp.aread()
                        raise ProviderError(resp.status_code, _error_from_body(body, resp.status_code))
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if not data:
                            continue
                        if data == "[DONE]":
                            break
                        try:
                            yield json.loads(data)
                        except json.JSONDecodeError:
                            continue
            except httpx.HTTPError as e:
                raise ProviderError(502, f"provider stream failed: {e}") from e
