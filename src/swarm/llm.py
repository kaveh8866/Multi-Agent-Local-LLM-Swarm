"""Client for a local OpenAI-compatible inference server (Ollama or vLLM).

Only the HTTP surface is used, so the same client works against:

    Ollama   http://llm-runtime:11434   -> /v1/chat/completions
    vLLM     http://llm-runtime:11434   -> /v1/chat/completions

The API key, when configured, is sent as a Bearer header and is never logged.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, Sequence

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
PROBE_TIMEOUT_SECONDS = 5.0

Opener = Callable[..., Any]


class LLMError(RuntimeError):
    """Raised when the inference server is unreachable or replies unusably."""


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120.0,
        opener: Opener | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        self.root_url = root
        self.chat_endpoint = f"{root}/v1/chat/completions"
        self.models_endpoint = f"{root}/v1/models"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._opener: Opener = opener or urllib.request.urlopen

    def chat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> str:
        """Return one completion for a chat transcript."""

        payload = {
            "model": self.model,
            "messages": [dict(message) for message in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        request = urllib.request.Request(
            self.chat_endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )

        try:
            with self._opener(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES)
        except urllib.error.HTTPError as exc:
            raise LLMError(f"inference server returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"inference server is unreachable: {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise LLMError(f"inference request failed: {exc}") from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LLMError("inference server returned malformed JSON") from exc

        try:
            content = parsed["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("inference response did not contain a chat completion") from exc

        if not isinstance(content, str) or not content.strip():
            raise LLMError("inference server returned an empty completion")
        return content

    def probe(self) -> tuple[bool, str]:
        """Cheap liveness check used by ``/ready``; never raises."""

        request = urllib.request.Request(self.models_endpoint, headers=self._headers(), method="GET")
        try:
            with self._opener(request, timeout=min(PROBE_TIMEOUT_SECONDS, self.timeout)) as response:
                response.read(4096)
        except urllib.error.HTTPError as exc:
            return False, f"inference server returned HTTP {exc.code}"
        except urllib.error.URLError as exc:
            return False, f"inference server unreachable: {exc.reason}"
        except (TimeoutError, OSError) as exc:
            return False, f"inference server unreachable: {exc}"
        return True, "reachable"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


class ScriptedLLM:
    """Deterministic stand-in for unit tests; records how many calls it served."""

    def __init__(self, responses: Sequence[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def chat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> str:
        if self.calls >= len(self._responses):
            raise LLMError("scripted responses exhausted")
        self.calls += 1
        return self._responses[self.calls - 1]

    def probe(self) -> tuple[bool, str]:
        return True, "scripted"
