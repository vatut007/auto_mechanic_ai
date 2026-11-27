"""Smoke tests for the Stage-1 core: registry, agent prompt, endpoint basics.

Run: uv run pytest tests/ -q
No provider calls, no network — only local logic.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agent import build_messages
from app.config import load_registry
from app.main import app, registry


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_registry_aliases_unique_and_resolvable():
    reg = load_registry()
    assert reg.aliases, "registry must expose aliases"
    for alias in reg.aliases:
        pspec, mspec = reg.resolve(alias)
        assert pspec.name and mspec.id


def test_models_endpoint_lists_aliases(client):
    data = client.get("/v1/models").json()
    ids = {m["id"] for m in data["data"]}
    assert ids == set(registry.aliases)


def test_unknown_model_returns_404_with_list(client):
    resp = client.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 404
    assert "available" in resp.json()["error"]["message"]


def test_empty_messages_rejected(client):
    resp = client.post("/v1/chat/completions", json={"model": "deepseek-chat", "messages": []})
    assert resp.status_code == 400


def test_house_system_prompt_goes_first():
    msgs = build_messages([{"role": "user", "content": "вопрос"}])
    assert msgs[0]["role"] == "system"
    assert "Придумывать числа запрещено" in msgs[0]["content"]
    assert msgs[1]["role"] == "user"


def test_client_system_prompt_is_kept_after_house_rules():
    msgs = build_messages([
        {"role": "system", "content": "ты пиратский попугай"},
        {"role": "user", "content": "вопрос"},
    ])
    assert [m["role"] for m in msgs] == ["system", "system", "user"]
    assert "попугай" in msgs[1]["content"]
