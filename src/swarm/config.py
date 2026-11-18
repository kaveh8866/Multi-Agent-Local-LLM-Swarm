"""Environment-first settings for the swarm orchestrator.

Secrets (the orchestrator token, the LLM API key) are read from the process
environment only. This module never opens `.env`, never logs a credential and
never writes one to disk.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

DEFAULT_LLM_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_LLM_MODEL = "qwen2.5:3b-instruct"
DEFAULT_MAX_STEPS = 12
DEFAULT_MAX_AGENTS = 4
MAX_STEPS_CEILING = 64
MAX_AGENTS_CEILING = 16


class ConfigError(ValueError):
    """Raised when configuration is missing or internally inconsistent."""


def _text(values: Mapping[str, str], key: str, default: str) -> str:
    raw = values.get(key)
    if raw is None:
        return default
    value = raw.strip()
    return value or default


def _integer(values: Mapping[str, str], key: str, default: int, minimum: int, maximum: int) -> int:
    raw = values.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}.") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{key} must be between {minimum} and {maximum}, got {value}.")
    return value


def _number(values: Mapping[str, str], key: str, default: float, minimum: float, maximum: float) -> float:
    raw = values.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}.") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{key} must be between {minimum} and {maximum}, got {value}.")
    return value


def _http_url(values: Mapping[str, str], key: str, default: str) -> str:
    raw = _text(values, key, default)
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"{key} must be an absolute http(s) URL, got {raw!r}.")
    return raw.rstrip("/")


def _path(values: Mapping[str, str], key: str, default: str, root: Path) -> Path:
    raw = _text(values, key, default)
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else (root / candidate).resolve()


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings resolved once at process start."""

    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_model: str = DEFAULT_LLM_MODEL
    llm_api_key: str = ""
    llm_timeout_seconds: float = 120.0
    max_agents: int = DEFAULT_MAX_AGENTS
    max_steps: int = DEFAULT_MAX_STEPS
    task_timeout_seconds: float = 300.0
    temperature: float = 0.2
    config_dir: Path = Path("configs")
    state_dir: Path = Path("state")
    server_port: int = 8080
    orchestrator_token: str = ""
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, root: Path | None = None) -> "Settings":
        """Build settings from an environment mapping.

        A fully empty mapping is valid: every value falls back to a documented
        default, which keeps offline tooling and unit tests runnable.
        """

        values = os.environ if env is None else env
        base = (root or Path.cwd()).resolve()

        return cls(
            llm_base_url=_http_url(values, "SWARM_LLM_BASE_URL", DEFAULT_LLM_BASE_URL),
            llm_model=_text(values, "SWARM_LLM_MODEL", DEFAULT_LLM_MODEL),
            llm_api_key=(values.get("SWARM_LLM_API_KEY") or "").strip(),
            llm_timeout_seconds=_number(values, "SWARM_LLM_TIMEOUT_SECONDS", 120.0, 1.0, 900.0),
            max_agents=_integer(values, "SWARM_MAX_AGENTS", DEFAULT_MAX_AGENTS, 1, MAX_AGENTS_CEILING),
            max_steps=_integer(values, "SWARM_MAX_STEPS", DEFAULT_MAX_STEPS, 1, MAX_STEPS_CEILING),
            task_timeout_seconds=_number(values, "SWARM_TASK_TIMEOUT_SECONDS", 300.0, 5.0, 3600.0),
            temperature=_number(values, "SWARM_TEMPERATURE", 0.2, 0.0, 2.0),
            config_dir=_path(values, "SWARM_CONFIG_DIR", "configs", base),
            state_dir=_path(values, "SWARM_STATE_DIR", "state", base),
            server_port=_integer(values, "SWARM_SERVER_PORT", 8080, 1, 65535),
            orchestrator_token=(values.get("SWARM_ORCHESTRATOR_TOKEN") or "").strip(),
            log_level=_text(values, "LOG_LEVEL", "INFO").upper(),
        )

    def require_token(self) -> str:
        """Return the orchestrator token or refuse to start."""

        if not self.orchestrator_token:
            raise ConfigError("SWARM_ORCHESTRATOR_TOKEN is not set; refusing to start the task API.")
        return self.orchestrator_token
