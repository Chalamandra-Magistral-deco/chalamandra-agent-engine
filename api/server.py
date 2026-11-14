"""API HTTP local del motor Chalamandra, implementada con la biblioteca estándar.

Por defecto solo escucha en 127.0.0.1. La ejecución de tareas que llaman modelos
está deshabilitada hasta configurarla expresamente y definir un token de API.
No se habilita CORS ni se expone una consola de shell.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import uuid
from datetime import datetime, timezone
from api import acceptance_store
from api import subscription_store
from api import webhook_dispatcher
from api import task_bus
from api.task_contracts import validate_task_contract
from api import candidate_store
from api import trace_store
from api.trace_parser import parse_traceparent
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
    """Allow loopback, plus explicit wildcard binding inside a controlled container."""
    if host == "0.0.0.0" and os.getenv("ALLOW_CONTAINER_BIND", "").strip().lower() == "true":
        return True
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
    request_queue_size = 128

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        master: Master | None = None,
        api_token: str | None = None,
        model_calls_enabled: bool = False,
        magis_execution_enabled: bool = False,
    ) -> None:
        host = str(server_address[0])
        self.host_is_loopback = _is_loopback_host(host)
        self.api_token = api_token.strip() if api_token and api_token.strip() else None
        self.model_calls_enabled = model_calls_enabled
        self.magis_execution_enabled = magis_execution_enabled
        self.master = master or Master()
        self.execution_lock = threading.Lock()
        raw_limit = os.getenv("CHALAMANDRA_API_MAX_CONCURRENCY", "64")
        try:
            self.max_concurrency = int(raw_limit)
        except ValueError as exc:
            raise ValueError("CHALAMANDRA_API_MAX_CONCURRENCY debe ser entero") from exc
        if not 4 <= self.max_concurrency <= 512:
            raise ValueError("CHALAMANDRA_API_MAX_CONCURRENCY debe estar entre 4 y 512")
        self._request_slots = threading.BoundedSemaphore(self.max_concurrency)
        super().__init__(server_address, handler_class)

    def process_request(self, request, client_address):
        # Back-pressure at accept time: never create an unbounded number of threads.
        self._request_slots.acquire()
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


class EngineRequestHandler(BaseHTTPRequestHandler):
    """Rutas HTTP explícitas; no expone ejecución de comandos arbitrarios."""

    server: EngineHTTPServer
    server_version = "ChalamandraAgentEngine"
    sys_version = ""

    def log_message(self, format_string: str, *args: Any) -> None:
        # No se registran cuerpos, tokens, cabeceras de autorización ni query strings.
        raw_client = self.client_address[0]
        client = raw_client.decode("utf-8", "replace") if isinstance(raw_client, bytes) else raw_client
        safe_path = urlsplit(self.path).path
        trace_id = getattr(self, "trace_id", None)
        parsed_parent = parse_traceparent(self.headers.get("traceparent", ""))
        if not trace_id:
            trace_id = parsed_parent["trace_id"] if parsed_parent else uuid.uuid4().hex
        status = None
        if len(args) > 1:
            try:
                status = int(args[1])
            except (TypeError, ValueError):
                status = None
        try:
            trace_store.record_request(
                trace_id=trace_id,
                traceparent=self.headers.get("traceparent") if parsed_parent else None,
                method=self.command,
                path=safe_path,
                status=status,
                client_ip=client,
            )
        except OSError as exc:
            # Trace storage failure is observable; never print request bodies or secrets.
            print(f"trace_store_error trace_id={trace_id} error={type(exc).__name__}")
        print(f"api {client} trace_id={trace_id} {self.command} {safe_path} status={status or '-'}")

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
        self.send_header("X-Trace-Id", getattr(self, "trace_id", ""))
        self.end_headers()
        self.wfile.write(encoded)

    def _authorized(
        self,
        *,
        require_token: bool = True,
        required_scope: str | None = None,
        required_subject: str | None = None,
    ) -> bool:
        if not require_token:
            return True
        import jwt
        auth = self.headers.get("Authorization", "")
        scheme, separator, credentials = auth.partition(" ")
        if (
            not separator
            or scheme.casefold() != "bearer"
            or not credentials
            or credentials != credentials.strip()
            or any(character.isspace() for character in credentials)
        ):
            return False
        token = credentials
        secret_env = {
            "catedralx-service": "CHALAMANDRA_CATEDRALX_JWT_SECRET",
            "magis-service": "CHALAMANDRA_MAGIS_JWT_SECRET",
        }.get(required_subject or "", "CHALAMANDRA_JWT_SECRET")
        secret = os.environ.get(secret_env, "")
        if not secret or not token:
            return False
        try:
            payload = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp"]})
            if required_scope:
                scopes = payload.get("scope", "").split()
                if required_scope not in scopes:
                    return False
            if required_subject and payload.get("sub") != required_subject:
                return False
            return True
        except jwt.PyJWTError:
            return False

    def _get_trace_id(self) -> str:
        """Return the incoming W3C trace ID or generate a new local trace ID."""
        parsed = parse_traceparent(self.headers.get("traceparent", ""))
        return parsed["trace_id"] if parsed else uuid.uuid4().hex

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
        self.trace_id = self._get_trace_id()
        path = urlsplit(self.path).path

        # Product-scoped MAGIS read contract. A dedicated service JWT can only
        # inspect MAGIS-owned task/result records; it cannot use general admin routes.
        if path.startswith("/internal/v1/products/magis/jobs/"):
            if not self._authorized(
                require_token=True,
                required_scope="magis:read",
                required_subject="magis-service",
            ):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            task_id = path.rsplit("/", 1)[-1]
            job = task_bus.get_task_status(task_id)
            if not job or not task_bus.is_magis_task(job):
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "magis_job_not_found"})
                return
            self._send_json(HTTPStatus.OK, {
                "job_id": task_id,
                "status": job.get("status", "queued"),
                "trace_id": job.get("trace_id", ""),
                "result_id": job.get("result_id"),
                "updated_at": job.get("updated_at", ""),
            })
            return

        if path.startswith("/internal/v1/products/magis/results/"):
            if not self._authorized(
                require_token=True,
                required_scope="magis:read",
                required_subject="magis-service",
            ):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            result_id = path.rsplit("/", 1)[-1]
            if not task_bus.is_magis_result(result_id):
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "magis_result_not_found"})
                return
            result = candidate_store.get_candidate(result_id)
            if not result:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "result_not_found"})
                return
            try:
                acceptance = acceptance_store.get_acceptance(result_id)
            except (OSError, ValueError):
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "acceptance_store_unavailable"})
                return
            if not acceptance:
                self._send_json(HTTPStatus.ACCEPTED, {
                    "status": "pending_verification",
                    "message": "Result exists but awaits independent acceptance",
                })
                return
            if acceptance.get("outcome") != "accepted":
                self._send_json(HTTPStatus.FORBIDDEN, {
                    "error": "result_not_accepted",
                    "outcome": acceptance.get("outcome"),
                    "reasons": acceptance.get("reasons", []),
                })
                return
            delivered = dict(result)
            delivered["delivery_status"] = "accepted"
            delivered["acceptance"] = {
                "outcome": "accepted",
                "decision_id": acceptance.get("decision_id"),
                "verifier_id": acceptance.get("verifier_id"),
                "decided_at": acceptance.get("decided_at"),
                "trace_id": acceptance.get("trace_id"),
            }
            self._send_json(HTTPStatus.OK, delivered)
            return
        if path.startswith("/v1/results/"):
            if not self._authorized(require_token=True, required_scope="results:read"):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            result_id = path.split("/")[-1]
            # P0-5 / P1-6: Buscar en el candidate_store unificado
            result = candidate_store.get_candidate(result_id)
            if not result:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "result_not_found"})
                return
            
            # FASE 2: Gate de Aceptación Independiente. Store corruption fails closed.
            try:
                acceptance = acceptance_store.get_acceptance(result_id)
            except (OSError, ValueError):
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "acceptance_store_unavailable"})
                return
            if not acceptance:
                self._send_json(HTTPStatus.ACCEPTED, {
                    "status": "pending_verification",
                    "message": "Result exists but awaits independent acceptance"
                })
                return
            if acceptance.get("outcome") != "accepted":
                self._send_json(HTTPStatus.FORBIDDEN, {
                    "error": "result_not_accepted",
                    "outcome": acceptance.get("outcome"),
                    "reasons": acceptance.get("reasons", [])
                })
                return
            delivered = dict(result)
            delivered["delivery_status"] = "accepted"
            delivered["acceptance"] = {
                "outcome": "accepted",
                "decision_id": acceptance.get("decision_id"),
                "verifier_id": acceptance.get("verifier_id"),
                "decided_at": acceptance.get("decided_at"),
                "trace_id": acceptance.get("trace_id"),
            }
            self._send_json(HTTPStatus.OK, delivered)
            return

        if path.startswith("/v1/jobs/"):
            if not self._authorized(require_token=True, required_scope="jobs:read"):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            job_id = path.split("/")[-1]
            job = task_bus.get_task_status(job_id)
            if not job:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "job_not_found"})
                return
            self._send_json(HTTPStatus.OK, {
                "job_id": job_id,
                "status": job.get("status", "queued"),
                "trace_id": job.get("trace_id", ""),
                "updated_at": job.get("updated_at", ""),
                "result_id": job.get("result_id"),
                "reason_code": None,
                "message": None
            })
            return

        if path == "/v1/subscriptions":
            if not self._authorized(require_token=True, required_scope="subscriptions:read"):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            subs = subscription_store.list_subscriptions()
            self._send_json(HTTPStatus.OK, {"items": subs})
            return

        if path.startswith("/internal/v1/orchestration/runs/"):
            if not self._authorized(require_token=True, required_scope="orchestration:read"):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            run_id = path.split("/")[-1]
            run_tasks = task_bus.get_tasks_by_run(run_id)
            if not run_tasks:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "run_not_found"})
                return
            task_statuses = [t.get("status", "queued") for t in run_tasks]
            terminal = {"candidate", "blocked", "failed", "completed", "accepted", "rejected"}
            if not all(status_value in terminal for status_value in task_statuses):
                status = "executing"
            elif any(status_value in {"blocked", "failed", "rejected"} for status_value in task_statuses):
                status = "completed_with_errors"
            elif any(status_value == "candidate" for status_value in task_statuses):
                status = "awaiting_acceptance"
            elif all(status_value == "accepted" for status_value in task_statuses):
                status = "accepted"
            else:
                status = "completed"
            self._send_json(HTTPStatus.OK, {
                "run_id": run_id,
                "status": status,
                "task_ids": [t["task_id"] for t in run_tasks],
                "task_statuses": task_statuses,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "trace_id": run_tasks[0].get("trace_id", "")
            })
            return

        if path == "/healthz":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "service": SERVICE_NAME,
                    "execution_enabled": self.server.model_calls_enabled,
                    "magis_execution_enabled": self.server.magis_execution_enabled,
                },
            )
            return
        if path == "/v1/inspect":
            if not self._authorized(require_token=True, required_scope="results:read"):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            if not self._authorized(require_token=True):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            self._send_json(HTTPStatus.OK, self.server.master.inspect())
            return
        if path == "/v1/agents":
            if not self._authorized(require_token=True, required_scope="results:read"):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            data = self.server.master.inspect()
            self._send_json(HTTPStatus.OK, {"agents": data.get("agents", [])})
            return
        if path == "/v1/tools":
            if not self._authorized(require_token=True, required_scope="results:read"):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
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

    def do_DELETE(self) -> None:
        self.trace_id = self._get_trace_id()
        path = urlsplit(self.path).path
        if not self._authorized(require_token=True):
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return

        if path.startswith("/v1/subscriptions/"):
            if not self._authorized(
                require_token=True,
                required_scope="subscriptions:write",
            ):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            sub_id = path.split("/")[-1]
            if subscription_store.disable_subscription(sub_id):
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "subscription_not_found"})
            return
            
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        self.trace_id = self._get_trace_id()
        path = urlsplit(self.path).path

        # MAGIS is a product client, not an administrator. Its dedicated JWT
        # can enqueue only this bounded text-analysis contract.
        if path == "/internal/v1/products/magis/requests":
            if not self._authorized(
                require_token=True,
                required_scope="magis:execute",
                required_subject="magis-service",
            ):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            if not self.server.magis_execution_enabled:
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "magis_execution_disabled"})
                return
            body = self._read_json_body()
            if body is None:
                return
            if set(body).difference({"operation_id", "prompt", "context"}):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "unexpected_fields"})
                return
            operation_id = body.get("operation_id")
            prompt = body.get("prompt")
            context = body.get("context", "")
            try:
                valid_operation_id = (
                    isinstance(operation_id, str)
                    and str(uuid.UUID(operation_id)) == operation_id.lower()
                )
            except (ValueError, AttributeError):
                valid_operation_id = False
            if not valid_operation_id:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_operation_id"})
                return
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_prompt"})
                return
            if not isinstance(context, str) or len(context) > 8000:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_context"})
                return
            idem_key = self.headers.get("Idempotency-Key", "")
            if idem_key != f"magis-{operation_id}":
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_idempotency_key"})
                return
            contract = {
                "task_id": str(uuid.uuid4()),
                "run_id": str(uuid.uuid4()),
                "objective": "magis_text_analysis",
                "product_code": "magis",
                "required_tools": [],
                "success_criteria": [
                    "Return a structured analysis of the supplied input.",
                    "Separate evidence, inference, assumptions and unknowns.",
                    "Do not perform external actions or claim unverified outcomes.",
                ],
                "payload": {
                    "operation_id": operation_id,
                    "prompt": prompt.strip(),
                    "context": context,
                },
                "trace_id": self.trace_id,
            }
            try:
                task_id = task_bus.enqueue_task(contract, idempotency_key=idem_key)
            except ValueError as exc:
                self._send_json(HTTPStatus.CONFLICT, {
                    "error": "idempotency_conflict",
                    "detail": str(exc)[:240],
                })
                return
            existing_task = task_bus.get_task_status(task_id)
            if not existing_task or not task_bus.is_magis_task(existing_task):
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "magis_task_persistence_failed"})
                return
            actual_run_id = existing_task.get("run_id")
            self._send_json(HTTPStatus.ACCEPTED, {
                "run_id": actual_run_id,
                "task_id": task_id,
                "status": existing_task.get("status", "queued"),
                "trace_id": existing_task.get("trace_id") or self.trace_id,
                "idempotent_replay": actual_run_id != contract["run_id"],
            })
            return

        # Create a persisted subscription; production SSRF policy remains in subscription_store.
        if path == "/v1/subscriptions":
            if not self._authorized(require_token=True, required_scope="subscriptions:write"):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            body = self._read_json_body()
            if body is None:
                return
            endpoint_url = body.get("endpoint_url")
            event_types = body.get("event_types")
            if (
                not isinstance(endpoint_url, str)
                or not endpoint_url.strip()
                or len(endpoint_url) > 2048
                or not isinstance(event_types, list)
                or not event_types
                or len(event_types) > 8
                or any(not isinstance(item, str) for item in event_types)
                or len(set(event_types)) != len(event_types)
                or any(item != "result.accepted" for item in event_types)
            ):
                self._send_json(HTTPStatus.BAD_REQUEST, {
                    "error": "invalid_subscription",
                    "required_fields": ["endpoint_url", "event_types"],
                    "allowed_event_types": ["result.accepted"],
                })
                return
            try:
                subscription = subscription_store.create_subscription(
                    endpoint_url=endpoint_url.strip(),
                    event_types=event_types,
                )
            except (ValueError, OSError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {
                    "error": "subscription_rejected",
                    "reason": str(exc)[:240],
                })
                return
            self._send_json(HTTPStatus.CREATED, subscription)
            return

        # Internal distribution is gated by an independently verified acceptance.
        if path == "/internal/v1/distributions":
            if not self._authorized(
                require_token=True,
                required_scope="results:publish",
                required_subject="catedralx-service",
            ):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            body = self._read_json_body()
            if body is None:
                return
            supplied_result_id = body.get("result_id")
            supplied_candidate_id = body.get("candidate_id")
            if (
                supplied_result_id is not None
                and supplied_candidate_id is not None
                and supplied_result_id != supplied_candidate_id
            ):
                self._send_json(HTTPStatus.BAD_REQUEST, {
                    "error": "distribution_identity_mismatch",
                })
                return
            result_id = supplied_result_id or supplied_candidate_id
            event_type = body.get("event_type", "result.accepted")
            if not isinstance(result_id, str) or not result_id.strip():
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "result_id_required"})
                return
            if event_type != "result.accepted":
                self._send_json(HTTPStatus.BAD_REQUEST, {
                    "error": "unsupported_distribution_event",
                    "allowed_event_type": "result.accepted",
                })
                return
            candidate = candidate_store.get_candidate(result_id)
            if not candidate:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "result_not_found"})
                return
            try:
                acceptance = acceptance_store.get_acceptance(result_id)
            except (OSError, ValueError):
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                    "error": "acceptance_store_unavailable",
                })
                return
            if not acceptance or acceptance.get("outcome") != "accepted":
                self._send_json(HTTPStatus.CONFLICT, {
                    "error": "result_not_independently_accepted",
                    "delivery_status": "pending_verification",
                })
                return
            task_states = task_bus.get_result_task_statuses(result_id)
            if not task_states or any(item.get("status") != "accepted" for item in task_states):
                self._send_json(HTTPStatus.CONFLICT, {
                    "error": "acceptance_operational_sync_pending",
                    "task_states": task_states,
                    "delivery_status": "blocked",
                })
                return
            trace_id = acceptance.get("trace_id") or candidate.get("trace_id")
            if not isinstance(trace_id, str) or len(trace_id) != 32:
                self._send_json(HTTPStatus.CONFLICT, {"error": "accepted_result_trace_id_invalid"})
                return
            requested_trace_id = body.get("trace_id")
            if requested_trace_id is not None and requested_trace_id != trace_id:
                self._send_json(HTTPStatus.CONFLICT, {"error": "trace_id_mismatch"})
                return
            parsed_parent = parse_traceparent(self.headers.get("traceparent", ""))
            if parsed_parent and parsed_parent["trace_id"] != trace_id:
                self._send_json(HTTPStatus.CONFLICT, {"error": "traceparent_trace_id_mismatch"})
                return
            event_id = body.get("event_id") or str(uuid.uuid4())
            if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 128:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_event_id"})
                return
            try:
                delivered_count = webhook_dispatcher.dispatch_event(
                    event_type="result.accepted",
                    result_id=result_id,
                    trace_id=trace_id,
                    traceparent=self.headers.get("traceparent") if parsed_parent else None,
                    event_id=event_id,
                )
            except (OSError, RuntimeError, ValueError):
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                    "error": "distribution_not_configured_or_unavailable",
                    "trace_id": trace_id,
                })
                return
            if delivered_count < 1:
                self._send_json(HTTPStatus.BAD_GATEWAY, {
                    "error": "no_distribution_delivery_succeeded",
                    "delivered_count": delivered_count,
                    "trace_id": trace_id,
                })
                return
            self._send_json(HTTPStatus.ACCEPTED, {
                "event_id": event_id,
                "event_type": "result.accepted",
                "result_id": result_id,
                "status": "dispatched",
                "delivered_count": delivered_count,
                "trace_id": trace_id,
            })
            return

        if path == "/v1/acceptance":
            if not self._authorized(require_token=True, required_scope="acceptance:write"):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            # Endpoint interno para el verificador independiente
            body = self._read_json_body()
            if not body or "candidate_id" not in body or "outcome" not in body:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_payload"})
                return
            try:
                if "signature" not in body:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "missing_signature"})
                    return
                candidate_id = str(body.get("candidate_id") or "")
                existing = acceptance_store.get_acceptance(candidate_id)
                replay = existing is not None
                if existing:
                    replay_payload = {
                        key: body.get(key) for key in acceptance_store._CANONICAL_FIELDS
                    }
                    replay_payload["signature"] = body.get("signature")
                    same_decision = existing == replay_payload
                    if not same_decision:
                        self._send_json(HTTPStatus.CONFLICT, {"error": "acceptance_already_recorded"})
                        return
                    decision_id = str(existing["decision_id"])
                    outcome = existing.get("outcome")
                else:
                    decision_id = acceptance_store.record_acceptance(body)
                    existing = acceptance_store.get_acceptance(candidate_id)
                    if not existing:
                        raise ValueError("La decisión registrada no pudo recuperarse del ledger")
                    outcome = existing.get("outcome")

                # An accepted decision is not distributable until TaskBus confirms
                # that every task linked to this result is in the accepted state.
                if outcome == "accepted":
                    transitioned = task_bus.mark_result_accepted(candidate_id)
                    task_states = task_bus.get_result_task_statuses(candidate_id)
                    all_tasks_accepted = bool(task_states) and all(
                        item.get("status") == "accepted" for item in task_states
                    )
                    if not all_tasks_accepted:
                        self._send_json(HTTPStatus.CONFLICT, {
                            "error": "acceptance_operational_sync_pending",
                            "decision_id": decision_id,
                            "transitioned_tasks": transitioned,
                            "task_states": task_states,
                            "distribution_status": "blocked",
                        })
                        return

                    # Stable event identity makes retries safe after a persisted
                    # decision or a crash between state transition and dispatch.
                    try:
                        delivered_count = webhook_dispatcher.dispatch_event(
                            event_type="result.accepted",
                            result_id=candidate_id,
                            trace_id=existing["trace_id"],
                            event_id=f"acceptance:{decision_id}",
                        )
                    except (OSError, RuntimeError, ValueError):
                        self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                            "error": "acceptance_recorded_distribution_pending",
                            "decision_id": decision_id,
                            "distribution_status": "pending_retry",
                        })
                        return
                    if delivered_count < 1:
                        self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                            "error": "acceptance_recorded_distribution_pending",
                            "decision_id": decision_id,
                            "delivered_count": delivered_count,
                            "distribution_status": "pending_retry",
                        })
                        return

                elif outcome == "rejected":
                    transitioned = task_bus.mark_result_rejected(candidate_id)
                    task_states = task_bus.get_result_task_statuses(candidate_id)
                    all_tasks_rejected = bool(task_states) and all(
                        item.get("status") == "rejected" for item in task_states
                    )
                    if not all_tasks_rejected:
                        self._send_json(HTTPStatus.CONFLICT, {
                            "error": "rejection_operational_sync_pending",
                            "decision_id": decision_id,
                            "transitioned_tasks": transitioned,
                            "task_states": task_states,
                            "distribution_status": "not_applicable",
                        })
                        return

                response = {"decision_id": decision_id, "outcome": outcome}
                if replay:
                    response["idempotent_replay"] = True
                self._send_json(HTTPStatus.CREATED, response)
            except ValueError as e:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path in {"/internal/v1/orchestration/runs", "/v1/request"}:
            if path == "/v1/request":
                authorized = self._authorized(
                    require_token=True,
                    required_scope="jobs:write",
                )
            else:
                authorized = self._authorized(
                    require_token=True,
                    required_scope="orchestration:run",
                    required_subject="catedralx-service",
                )
            if not authorized:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            body = self._read_json_body()
            if body is None:
                return
            objective = body.get("objective")
            if not isinstance(objective, str) or not objective.strip() or len(objective) > MAX_TASK_CHARS:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_objective"})
                return

            payload = body.get("payload", {})
            if not isinstance(payload, dict):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "payload_must_be_object"})
                return
            if objective.strip() == "catedralx_economic_proposal_review":
                if path != "/internal/v1/orchestration/runs":
                    self._send_json(HTTPStatus.FORBIDDEN, {
                        "error": "economic_proposal_requires_authenticated_catedralx_service"
                    })
                    return
                # This metadata is written only after JWT subject+scope verification.
                # Caller-supplied values are discarded and cannot assert their own ingress.
                payload = dict(payload)
                payload["_agents_ingress"] = {
                    "subject": "catedralx-service",
                    "scope": "orchestration:run",
                    "verified": True,
                }
            contract_error = validate_task_contract(objective, payload)
            if contract_error:
                self._send_json(HTTPStatus.BAD_REQUEST, {
                    "error": "invalid_task_contract",
                    "detail": contract_error,
                })
                return
            permissions = body.get("permissions", {})
            if not isinstance(permissions, dict):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "permissions_must_be_object"})
                return
            if set(permissions) != {"allowed_tools", "side_effects"}:
                self._send_json(HTTPStatus.BAD_REQUEST, {
                    "error": "permissions_require_allowed_tools_and_side_effects"
                })
                return
            allowed_tools = permissions.get("allowed_tools")
            side_effects = permissions.get("side_effects")
            if not isinstance(allowed_tools, list) or any(
                not isinstance(tool, str) or not tool.strip() or len(tool) > 128
                for tool in allowed_tools
            ) or len(allowed_tools) > 32 or len(set(allowed_tools)) != len(allowed_tools):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_allowed_tools"})
                return
            if side_effects not in {"none", "read_only", "approved_scoped"}:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_side_effects_policy"})
                return
            registered_tools = set(self.server.master.tools.available())
            unknown_tools = sorted(set(allowed_tools).difference(registered_tools))
            if unknown_tools:
                self._send_json(HTTPStatus.BAD_REQUEST, {
                    "error": "unknown_allowed_tools",
                    "tools": unknown_tools,
                })
                return
            if side_effects == "approved_scoped":
                self._send_json(HTTPStatus.FORBIDDEN, {
                    "error": "approved_scoped_requires_verifiable_approval",
                    "execution_status": "blocked",
                })
                return
            if allowed_tools:
                self._send_json(HTTPStatus.FORBIDDEN, {
                    "error": "tool_effect_classification_missing",
                    "tools": sorted(allowed_tools),
                    "execution_status": "blocked",
                })
                return
            success_criteria = body.get("success_criteria", ["Task completed"])
            if not isinstance(success_criteria, list) or not success_criteria or any(
                not isinstance(item, str) or not item.strip() or len(item) > 1000
                for item in success_criteria
            ) or len(success_criteria) > 20:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_success_criteria"})
                return

            request_idem_key = self.headers.get("Idempotency-Key")
            if path == "/v1/request" and not request_idem_key:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "idempotency_key_required"})
                return
            if request_idem_key is not None and (
                not request_idem_key.strip() or len(request_idem_key) < 8 or len(request_idem_key) > 128
                or "\r" in request_idem_key or "\n" in request_idem_key
            ):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_idempotency_key"})
                return

            run_id = str(uuid.uuid4())
            task_id = str(uuid.uuid4())
            trace_id = self.trace_id
            contract = {
                "task_id": task_id,
                "run_id": run_id,
                "objective": objective.strip(),
                "required_tools": allowed_tools,
                "execution_policy": {
                    "allowed_tools": allowed_tools,
                    "side_effects": side_effects,
                },
                "success_criteria": success_criteria,
                "payload": payload,
                "trace_id": trace_id,
            }
            try:
                task_id = task_bus.enqueue_task(contract, idempotency_key=request_idem_key)
            except ValueError as exc:
                self._send_json(HTTPStatus.CONFLICT, {
                    "error": "idempotency_conflict",
                    "detail": str(exc)[:500],
                })
                return
            existing_task = task_bus.get_task_status(task_id)
            if not existing_task:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "task_persistence_failed"})
                return
            actual_run_id = existing_task.get("run_id")
            if actual_run_id is None:
                self._send_json(HTTPStatus.CONFLICT, {"error": "idempotency_key_bound_to_non_run_task"})
                return
            self._send_json(HTTPStatus.ACCEPTED, {
                "run_id": actual_run_id,
                "task_id": task_id,
                "status": existing_task.get("status", "queued"),
                "trace_id": existing_task.get("trace_id") or trace_id,
                "idempotent_replay": actual_run_id != run_id,
            })
            return

        if path.startswith("/internal/v1/tasks/") and path.endswith("/results"):
            if not self._authorized(require_token=True, required_scope="tasks:complete"):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            task_id = path.split("/")[-2]
            body = self._read_json_body()
            if not isinstance(body, dict) or not body:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_payload"})
                return
            lease_token = body.get("lease_token")
            candidate_result = {key: value for key, value in body.items() if key != "lease_token"}
            if not isinstance(lease_token, str) or not lease_token.strip():
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "lease_token_required"})
                return
            if candidate_result.get("status") not in {"candidate", "blocked", "failed"}:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_candidate_status"})
                return
            required_fields = {
                "candidate_id", "result_id", "worker_id", "status", "summary",
                "evidence", "assumptions", "contradictions", "trace_id",
            }
            missing_fields = sorted(required_fields.difference(candidate_result))
            if missing_fields:
                self._send_json(HTTPStatus.BAD_REQUEST, {
                    "error": "candidate_result_missing_fields",
                    "missing_fields": missing_fields,
                })
                return
            allowed_fields = required_fields | {"tests_performed", "limitations"}
            unknown_fields = sorted(set(candidate_result).difference(allowed_fields))
            if unknown_fields:
                self._send_json(HTTPStatus.BAD_REQUEST, {
                    "error": "candidate_result_unknown_fields",
                    "unknown_fields": unknown_fields,
                })
                return
            for field in ("candidate_id", "worker_id", "summary", "trace_id"):
                value = candidate_result.get(field)
                if not isinstance(value, str) or not value.strip():
                    self._send_json(HTTPStatus.BAD_REQUEST, {
                        "error": "invalid_candidate_result_field", "field": field,
                    })
                    return
            for field in ("evidence", "assumptions", "contradictions", "tests_performed", "limitations"):
                if field in candidate_result and not isinstance(candidate_result[field], list):
                    self._send_json(HTTPStatus.BAD_REQUEST, {
                        "error": "invalid_candidate_result_field", "field": field,
                    })
                    return
            result_id = candidate_result.get("result_id")
            if result_id is not None and (
                not isinstance(result_id, str) or not result_id.strip()
                or result_id != candidate_result["candidate_id"]
            ):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "candidate_result_identity_mismatch"})
                return
            if candidate_result["status"] == "candidate" and result_id is None:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "result_id_required_for_candidate"})
                return
            try:
                completed = task_bus.mark_task_completed(
                    task_id, candidate_result, result_id=result_id, lease_token=lease_token
                )
            except (TypeError, ValueError):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_candidate_result"})
                return
            if not completed:
                self._send_json(HTTPStatus.CONFLICT, {"error": "task_lease_invalid_or_expired"})
                return
            self._send_json(HTTPStatus.ACCEPTED, {
                "candidate_id": candidate_result.get("candidate_id"),
                "verification_status": "pending",
                "trace_id": candidate_result.get("trace_id")
            })
            return

        if path == "/v1/jobs":
            if not self._authorized(require_token=True, required_scope="jobs:write"):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            job_idem_key = self.headers.get("Idempotency-Key") or ""
            if len(job_idem_key) < 8:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_idempotency_key"})
                return
            body = self._read_json_body()
            if not isinstance(body, dict):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_payload"})
                return
            objective = body.get("type")
            payload = body.get("payload")
            if not isinstance(objective, str) or not objective.strip():
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "task_type_required"})
                return
            if not isinstance(payload, dict):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "payload_must_be_object"})
                return
            contract_error = validate_task_contract(objective, payload)
            if contract_error:
                self._send_json(HTTPStatus.BAD_REQUEST, {
                    "error": "invalid_task_contract",
                    "detail": contract_error,
                })
                return

            # Encolar en el TaskBus unificado únicamente tras validar el tipo y payload.
            trace_id = self.trace_id
            try:
                task_id = task_bus.enqueue_task(
                    {
                        "objective": objective.strip(),
                        "payload": payload,
                        "trace_id": trace_id
                    },
                    idempotency_key=job_idem_key
                )
            except ValueError as exc:
                # TaskBus lanza ValueError si mismo key + payload distinto
                self._send_json(HTTPStatus.CONFLICT, {
                    "error": "idempotency_conflict",
                    "detail": str(exc)[:500],
                })
                return
            self._send_json(HTTPStatus.ACCEPTED, {
                "job_id": task_id,
                "status": "queued",
                "trace_id": trace_id,
                "accepted_at": datetime.now(timezone.utc).isoformat()
            })
            return

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
            if not self._authorized(require_token=True, required_scope="jobs:write"):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
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
            if not self._authorized(require_token=True, required_scope="jobs:write"):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
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
    bind_host = host or os.getenv("CHALAMANDRA_API_HOST") or os.getenv("API_BIND_HOST") or "127.0.0.1"
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
    magis_execution_enabled = os.getenv("CHALAMANDRA_MAGIS_EXECUTION_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    magis_secret = os.getenv("CHALAMANDRA_MAGIS_JWT_SECRET", "").strip()

    if not _is_loopback_host(bind_host):
        raise ValueError(
            "La API solo puede enlazarse a loopback; para acceso remoto usa un túnel "
            "autenticado o proxy TLS, no HTTP directo."
        )
    if model_calls_enabled and token is None:
        raise ValueError(
            "CHALAMANDRA_API_TOKEN es obligatorio al habilitar llamadas a modelos"
        )
    if magis_execution_enabled and len(magis_secret) < 32:
        raise ValueError(
            "CHALAMANDRA_MAGIS_JWT_SECRET de al menos 32 caracteres es obligatorio al habilitar MAGIS"
        )

    return EngineHTTPServer(
        (bind_host, bind_port),
        EngineRequestHandler,
        master=master,
        api_token=token,
        model_calls_enabled=model_calls_enabled,
        magis_execution_enabled=magis_execution_enabled,
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

# NOTE: api.server uses the stdlib EngineHTTPServer, not a FastAPI application.
# api/distribution.py is an unintegrated draft and must not be included here.
