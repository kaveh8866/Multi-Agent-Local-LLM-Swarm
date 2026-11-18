"""HTTP surface for the swarm orchestrator.

    GET  /health     process + configuration liveness (no model call)
    GET  /ready      readiness: also probes the inference server
    POST /v1/tasks   run one task, bearer token required

The server is stdlib only. It is a control plane for a single orchestrator
process, not a public API: Compose publishes it on 127.0.0.1 only.
"""

from __future__ import annotations

import hmac
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

from . import VERSION
from .config import ConfigError, Settings
from .llm import LLMClient
from .orchestrator import Orchestrator
from .registry import RegistryError, Task, load_personas, load_workflows

SERVICE = "swarm-orchestrator"
MAX_REQUEST_BYTES = 256 * 1024

LOGGER = logging.getLogger("swarm.worker")


def is_authorized(header: str | None, token: str) -> bool:
    """Constant-time bearer-token check; an unset token authorizes nothing."""

    if not token or not header:
        return False
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return False
    return hmac.compare_digest(value.strip(), token)


class SwarmService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.personas = load_personas(settings.config_dir / "agents.yaml")
        self.workflows = load_workflows(settings.config_dir / "workflows.yaml", self.personas)
        self.token = settings.require_token()
        self.llm = LLMClient(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout_seconds,
        )
        self.orchestrator = Orchestrator(settings, self.personas, self.workflows, self.llm)

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "service": SERVICE,
            "version": VERSION,
            "model": self.settings.llm_model,
            "workflows": sorted(self.workflows),
            "personas": sorted(self.personas),
        }

    def readiness(self) -> tuple[int, dict[str, Any]]:
        reachable, detail = self.llm.probe()
        payload = {
            "status": "ready" if reachable else "degraded",
            "service": SERVICE,
            "llm": detail,
            "model": self.settings.llm_model,
        }
        return (200 if reachable else 503), payload

    def run_task(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        task = Task.from_dict(payload, known_workflows=self.workflows)
        dry_run = bool(payload.get("dry_run", False))
        return self.orchestrator.run(task, dry_run=dry_run).to_dict()


class SwarmHTTPServer(ThreadingHTTPServer):
    service: SwarmService

    def __init__(self, address: tuple[str, int], service: SwarmService) -> None:
        super().__init__(address, TaskApiHandler)
        self.service = service


class TaskApiHandler(BaseHTTPRequestHandler):
    server_version = f"cloudguard-swarm/{VERSION}"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler naming
        if self.path == "/health":
            self._respond(200, self.server.service.health())
        elif self.path == "/ready":
            code, payload = self.server.service.readiness()
            self._respond(code, payload)
        else:
            self._respond(404, {"status": "error", "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler naming
        service = self.server.service

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            self._respond(400, {"status": "error", "error": "invalid Content-Length"})
            return

        if self.path != "/v1/tasks":
            self._discard_body(length)
            self._respond(404, {"status": "error", "error": "not found"})
            return

        if not is_authorized(self.headers.get("Authorization"), service.token):
            # Consume the body first: an unread body would be parsed as the next
            # request line on a keep-alive connection.
            self._discard_body(length)
            self._respond(401, {"status": "error", "error": "missing or invalid orchestrator token"})
            return

        if length <= 0:
            self._respond(400, {"status": "error", "error": "request body is required"})
            return
        if length > MAX_REQUEST_BYTES:
            self._respond(413, {"status": "error", "error": "request body is too large"})
            return

        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._respond(400, {"status": "error", "error": "request body is not valid JSON"})
            return

        if not isinstance(payload, Mapping):
            self._respond(400, {"status": "error", "error": "request body must be a JSON object"})
            return

        try:
            result = service.run_task(payload)
        except (RegistryError, ValueError) as exc:
            self._respond(422, {"status": "error", "error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001 - keep the API alive on unexpected faults
            LOGGER.exception("task run failed")
            self._respond(500, {"status": "error", "error": f"task run failed: {type(exc).__name__}"})
            return

        self._respond(200, {"status": "ok", "result": result})

    def _discard_body(self, length: int) -> None:
        """Read and drop an unread request body, bounded by the request limit."""

        if length <= 0:
            return
        if length > MAX_REQUEST_BYTES:
            self.close_connection = True
            return
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 8192))
            if not chunk:
                self.close_connection = True
                return
            remaining -= len(chunk)

    def _respond(self, code: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        # Never log headers: the Authorization header carries the token.
        LOGGER.info("%s %s", self.address_string(), format % args)


def serve(settings: Settings | None = None) -> None:
    settings = settings or Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    service = SwarmService(settings)
    server = SwarmHTTPServer(("0.0.0.0", settings.server_port), service)

    print(
        json.dumps(
            {
                "event": "listening",
                "service": SERVICE,
                "version": VERSION,
                "port": settings.server_port,
                "model": settings.llm_model,
                "workflows": sorted(service.workflows),
                "personas": sorted(service.personas),
            }
        ),
        flush=True,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    try:
        serve()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
