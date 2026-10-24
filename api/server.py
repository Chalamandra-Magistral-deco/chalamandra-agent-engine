"""API HTTP local del motor Chalamandra, implementada con la biblioteca estándar.

Por defecto solo escucha en 127.0.0.1. La ejecución de tareas que llaman modelos
está deshabilitada hasta configurarla expresamente y definir un token de API.
No se habilita CORS ni se expone una consola de shell.
"""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import os
import threading
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from core.master import Master

SERVICE_NAME = "chalamandra-agent-engine"
MAX_BODY_BYTES = 64 * 1024
MAX_TASK_CHARS = 8_000


def _is_loopback_host(host: str) -> bool:
    """Only treat explicit loopback addresses/names as safe for no-token mode."""
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _jsonable(value: Any) -> Any:
    """Convert known structured result types to JSON-safe primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return _jsonable(value.model_dump())
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if hasattr(value, "tolist") and callable(value.tolist):
        return _jsonable(value.tolist())
    if hasattr(value, "item") and callable(value.item):
        return _jsonable(value.item())
    if hasattr(value, "isoformat") and callable(value.isoformat):
        return value.isoformat()
    raise TypeError(f"Resultado no serializable a JSON: {type(value).__name__}")


class EngineHTTPServer(ThreadingHTTPServer):
    """HTTP server con un Master compartido y ejecución mutante serializada."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        master: Master | None = None,
        api_token: str | None = None,
        model_calls_enabled: bool = False,
    ) -> None:
        host = str(server_address[0])
        self.host_is_loopback = _is_loopback_host(host)
        self.api_token = api_token.strip() if api_token and api_token.strip() else None
        self.model_calls_enabled = model_calls_enabled
        self.master = master or Master()
        self.execution_lock = threading.Lock()
        super().__init__(server_address, handler_class)


class EngineRequestHandler(BaseHTTPRequestHandler):
    """Rutas HTTP explícitas; no expone ejecución de comandos arbitrarios."""

    server: EngineHTTPServer
    server_version = "ChalamandraAgentEngine"
    sys_version = ""

    def log_message(self, format_string: str, *args: Any) -> None:
        # No se registran cuerpos, tokens ni cadenas de consulta.
        raw_client = self.client_address[0]
        client = raw_client.decode("utf-8", "replace") if isinstance(raw_client, bytes) else raw_client
        print(f"api {client} {self.command} {self.path.split('?', 1)[0]}")

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        encoded = json.dumps(
            _jsonable(payload),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def _authorized(self, *, require_token: bool = False) -> bool:
        token = self.server.api_token
        if token is None:
            # Read-only health/discovery can be local; every POST needs a token.
            return self.server.host_is_loopback and not require_token
        supplied = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not supplied.startswith(prefix):
            return False
        return hmac.compare_digest(supplied[len(prefix):], token)

    def _read_json_body(self) -> dict[str, Any] | None:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "content_type_must_be_json"})
            return None

        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            self._send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "content_length_required"})
            return None
        if length > MAX_BODY_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large"})
            return None

        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return None
        if not isinstance(body, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "json_object_required"})
            return None
        return body

    def do_GET(self) -> None:
        if not self._authorized():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return

        path = urlsplit(self.path).path
        if path == "/healthz":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "service": SERVICE_NAME,
                    "execution_enabled": self.server.model_calls_enabled,
                },
            )
            return
        if path == "/v1/inspect":
            self._send_json(HTTPStatus.OK, self.server.master.inspect())
            return
        if path == "/v1/agents":
            data = self.server.master.inspect()
            self._send_json(HTTPStatus.OK, {"agents": data.get("agents", [])})
            return
        if path == "/v1/tools":
            tools = []
            for name in self.server.master.tools.available():
                spec = self.server.master.tools.get(name)
                tools.append({
                    "name": spec.name,
                    "description": spec.description,
                    "skills": list(spec.skills),
                })
            self._send_json(HTTPStatus.OK, {"tools": tools})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        if not self._authorized(require_token=True):
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return

        path = urlsplit(self.path).path
        if path in {"/v1/plan", "/v1/tasks"} and not self.server.model_calls_enabled:
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {
                    "error": "model_calls_disabled",
                    "detail": "Enable with CHALAMANDRA_API_ENABLE_MODEL_CALLS=true and configure CHALAMANDRA_API_TOKEN.",
                },
            )
            return

        body = self._read_json_body()
        if body is None:
            return

        if path == "/v1/tool-calls":
            if set(body) != {"agent", "tool_call"}:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "expected_agent_and_tool_call"})
                return
            if not isinstance(body["agent"], str) or not isinstance(body["tool_call"], dict):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_agent_or_tool_call"})
                return
            try:
                with self.server.execution_lock:
                    result = asyncio.run(
                        self.server.master.execute_agent_tool_call(
                            body["agent"],
                            body["tool_call"],
                        )
                    )
                self._send_json(HTTPStatus.OK, {"status": "ok", "result": result})
            except PermissionError:
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "agent_tool_not_allowed"})
            except (TypeError, ValueError) as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "invalid_tool_call", "detail": str(exc)[:500]},
                )
            except Exception:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "tool_execution_failed"})
            return

        if path in {"/v1/plan", "/v1/tasks"}:
            if set(body) != {"task"} or not isinstance(body.get("task"), str):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "expected_task_string"})
                return
            task = body["task"].strip()
            if not task or len(task) > MAX_TASK_CHARS:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "task_empty_or_too_long"})
                return
            method = self.server.master.plan if path == "/v1/plan" else self.server.master.execute_task
            try:
                with self.server.execution_lock:
                    result = asyncio.run(method(task))
                self._send_json(HTTPStatus.OK, {"status": "ok", "result": result})
            except Exception:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "task_execution_failed"})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})


def create_server(
    host: str | None = None,
    port: int | None = None,
    *,
    master: Master | None = None,
) -> EngineHTTPServer:
    """Create the server after validating its network and execution policy."""
    bind_host = host or os.getenv("CHALAMANDRA_API_HOST") or "127.0.0.1"
    raw_port: str | int = port if port is not None else os.getenv("CHALAMANDRA_API_PORT", "8766")
    try:
        bind_port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ValueError("CHALAMANDRA_API_PORT debe ser un entero") from exc
    if not 0 <= bind_port <= 65535:
        raise ValueError("CHALAMANDRA_API_PORT fuera de rango")

    token = os.getenv("CHALAMANDRA_API_TOKEN", "").strip() or None
    model_calls_enabled = os.getenv("CHALAMANDRA_API_ENABLE_MODEL_CALLS", "").strip().lower() in {
        "1", "true", "yes", "on"
    }

    if not _is_loopback_host(bind_host):
        raise ValueError(
            "La API solo puede enlazarse a loopback; para acceso remoto usa un túnel "
            "autenticado o proxy TLS, no HTTP directo."
        )
    if model_calls_enabled and token is None:
        raise ValueError(
            "CHALAMANDRA_API_TOKEN es obligatorio al habilitar llamadas a modelos"
        )

    return EngineHTTPServer(
        (bind_host, bind_port),
        EngineRequestHandler,
        master=master,
        api_token=token,
        model_calls_enabled=model_calls_enabled,
    )


def main() -> None:
    server = create_server()
    address = server.server_address
    raw_host = address[0]
    display_host = raw_host.decode("utf-8", "replace") if isinstance(raw_host, bytes) else raw_host
    print(f"{SERVICE_NAME} listening on {display_host}:{address[1]}")
    print(
        "Model-backed /v1/plan and /v1/tasks are "
        + ("ENABLED" if server.model_calls_enabled else "DISABLED")
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("Shutdown requested")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
