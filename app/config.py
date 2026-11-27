"""Configuration loading: .env secrets + config/providers.yaml registry."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "providers.yaml"

# Local secrets from .env (never committed) become environment variables.
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class ModelSpec:
    id: str                       # real model id at the provider
    context_tokens: int | None
    capabilities: dict            # vision / tools / json_mode


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_url: str
    api_key_env: str | None
    models: dict[str, ModelSpec]  # public alias -> spec

    def api_key(self) -> str | None:
        if not self.api_key_env:
            return None
        return os.getenv(self.api_key_env, "").strip() or None

    def has_key(self) -> bool:
        return self.api_key() is not None


@dataclass(frozen=True)
class Registry:
    providers: dict[str, ProviderSpec]
    aliases: dict[str, tuple[str, ModelSpec]]  # alias -> (provider name, spec)
    embeddings: dict

    def resolve(self, alias: str) -> tuple[ProviderSpec, ModelSpec]:
        if alias not in self.aliases:
            raise KeyError(alias)
        pname, mspec = self.aliases[alias]
        return self.providers[pname], mspec


def load_registry(path: Path = CONFIG_PATH) -> Registry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    providers: dict[str, ProviderSpec] = {}
    aliases: dict[str, tuple[str, ModelSpec]] = {}
    for pname, pdata in (raw.get("providers") or {}).items():
        models: dict[str, ModelSpec] = {}
        for alias, m in (pdata.get("models") or {}).items():
            m = m or {}
            if alias in aliases:
                raise ValueError(f"duplicate model alias across providers: {alias!r}")
            models[alias] = ModelSpec(
                id=m.get("id") or alias,
                context_tokens=m.get("context_tokens"),
                capabilities=m.get("capabilities") or {},
            )
            aliases[alias] = (pname, models[alias])
        providers[pname] = ProviderSpec(
            name=pname,
            base_url=pdata["base_url"].rstrip("/"),
            api_key_env=pdata.get("api_key_env"),
            models=models,
        )
    return Registry(
        providers=providers,
        aliases=aliases,
        embeddings=raw.get("embeddings") or {},
    )


def db_path() -> Path:
    return Path(os.getenv("AMAI_DB_PATH", ROOT / "data" / "usage.db"))
