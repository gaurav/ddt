"""Which models the chat panel offers.

Models come from `ddt.toml`:

    [[models]]
    name = "qwen3 (local)"                      # label shown in the UI
    model = "qwen3:14b"                         # model id sent to the endpoint
    base_url = "http://localhost:11434/v1"      # any OpenAI-compatible endpoint
    api_key_env = "OPENROUTER_API_KEY"          # optional: env var holding the key

With no config file, DDT asks a local Ollama server which models it has.
"""

from __future__ import annotations

import json
import os
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/v1"


@dataclass(frozen=True)
class ModelConfig:
    name: str
    model: str
    base_url: str = OLLAMA_URL
    api_key_env: str | None = None

    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "") if self.api_key_env else "none"


def _ollama_models(base_url: str = OLLAMA_URL) -> list[ModelConfig]:
    try:
        with urllib.request.urlopen(f"{base_url}/models", timeout=1.5) as response:
            data = json.load(response)
    except (OSError, ValueError):
        return []
    ids = sorted(m["id"] for m in data.get("data", []) if isinstance(m, dict) and m.get("id"))
    return [ModelConfig(name=i, model=i, base_url=base_url) for i in ids]


def load_models(config_path: Path | None = None) -> tuple[list[ModelConfig], str]:
    """Return (models, where they came from)."""
    path = config_path or Path("ddt.toml")
    if path.is_file():
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
        models = []
        for entry in doc.get("models", []):
            model = entry["model"]
            models.append(
                ModelConfig(
                    name=entry.get("name", model),
                    model=model,
                    base_url=entry.get("base_url", OLLAMA_URL),
                    api_key_env=entry.get("api_key_env"),
                )
            )
        return models, str(path)
    if config_path:
        raise FileNotFoundError(f"model config not found: {config_path}")
    return _ollama_models(), f"Ollama at {OLLAMA_URL}"
