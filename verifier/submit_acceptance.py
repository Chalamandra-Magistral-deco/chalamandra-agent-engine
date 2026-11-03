#!/usr/bin/env python3
"""Submit an already-signed independent acceptance decision to the Agents API.

This client never signs decisions and never reads an Ed25519 private seed.
Provide a short-lived Bearer token in CHALAMANDRA_ACCEPTANCE_TOKEN and set
CHALAMANDRA_VERIFIER_PUBLIC_KEY to the public key trusted by the API.

Example (reviewed input JSON -> isolated signer -> API submitter):
  python verifier/independent_acceptance_verifier.py --confirm ACCEPT:<candidate_id> \
    < decision.json | env -u CHALAMANDRA_ED25519_SEED \
      CHALAMANDRA_ACCEPTANCE_URL=http://agents-api:8766/v1/acceptance \
      CHALAMANDRA_ACCEPTANCE_TOKEN='<short-lived-token>' \
      python verifier/submit_acceptance.py

A successful 201 confirms API acceptance only. Verify webhook delivery separately.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from api.crypto_signer import verify_decision  # noqa: E402

_CANONICAL_FIELDS = (
    "decision_id",
    "candidate_id",
    "outcome",
    "criteria_checked",
    "reasons",
    "missing_evidence",
    "verifier_id",
    "evidence_digest",
    "decided_at",
    "trace_id",
)
_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SIGNATURE_RE = re.compile(r"^[0-9a-fA-F]{128}$")
_MAX_INPUT_BYTES = 128 * 1024


def _emit(value: dict[str, Any], *, stream: Any = None) -> None:
    """
    Emite un objeto de diagnóstico como JSON por el stream seleccionado.

    Args:
        value (dict[str, Any]): Resultado estructurado que se quiere informar.
        stream (Any): Stream compatible con `write`; si es `None`, se usa la salida definida por el módulo.

    Returns:
        None.

    Raises:
        OSError: Si falla la escritura del stream.
        TypeError: Si el valor no se puede serializar a JSON.

    Side effects:
        Escribe una línea JSON; no debe recibir ni emitir tokens o secretos.

    Example:
        _emit({"status": "submission_failed", "error_type": "TimeoutError"})
    """

    if stream is None:
        stream = sys.stdout
    print(json.dumps(value, ensure_ascii=False, sort_keys=True), file=stream)


def _read_decision(input_path: str) -> dict[str, Any]:
    """
    Lee y valida el contrato de decisión firmada desde stdin o un archivo.

    Args:
        input_path (str): `-` para stdin o ruta de archivo que contiene JSON de decisión.

    Returns:
        dict[str, Any]: Decisión validada con campos requeridos, outcome y formato de firma admitido.

    Raises:
        ValueError: Si la entrada supera el límite, no es un objeto JSON válido, contiene campos faltantes/extra o incumple las reglas del contrato.
        OSError: Si no se puede abrir o leer el archivo indicado.
        json.JSONDecodeError: Normalmente se envuelve como `ValueError` de entrada inválida.

    Side effects:
        Lee entrada con tamaño acotado; no firma ni envía la decisión por sí sola.

    Example:
        decision = _read_decision("decision.json")
    """

    if input_path == "-":
        raw = sys.stdin.read(_MAX_INPUT_BYTES + 1)
    else:
        source = Path(input_path)
        if source.stat().st_size > _MAX_INPUT_BYTES:
            raise ValueError("decision_input_too_large")
        raw = source.read_text(encoding="utf-8")
    if len(raw.encode("utf-8")) > _MAX_INPUT_BYTES:
        raise ValueError("decision_input_too_large")
    try:
        decision = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("decision_input_invalid_json") from exc
    if not isinstance(decision, dict):
        raise ValueError("decision_must_be_json_object")
    missing = [field for field in _CANONICAL_FIELDS if field not in decision]
    if missing:
        raise ValueError("decision_missing_fields:" + ",".join(missing))
    extras = sorted(set(decision) - set(_CANONICAL_FIELDS) - {"signature"})
    if extras:
        raise ValueError("decision_unexpected_fields:" + ",".join(extras))
    if decision.get("outcome") not in {"accepted", "rejected"}:
        raise ValueError("decision_outcome_invalid")
    trace_id = decision.get("trace_id")
    if (
        not isinstance(trace_id, str)
        or not _TRACE_ID_RE.fullmatch(trace_id)
        or trace_id == "0" * 32
    ):
        raise ValueError("decision_trace_id_invalid")
    signature = decision.get("signature")
    if not isinstance(signature, str) or not _SIGNATURE_RE.fullmatch(signature):
        raise ValueError("decision_signature_invalid_format")
    return decision


def _validate_url(url: str) -> None:
    """
    Valida la URL de destino del endpoint de aceptación.

    Args:
        url (str): URL que debe usarse para el envío autenticado de una decisión.

    Returns:
        None: Continúa sin error si pasa el contrato de destino.

    Raises:
        ValueError: Si el esquema no es HTTP/HTTPS o la ruta no termina exactamente en `/v1/acceptance`, o contiene query/fragment prohibidos.

    Security:
        Previene destinos alternos no previstos para esta herramienta; no sigas redirecciones porque podría exponerse el bearer token.

    Example:
        _validate_url("https://api.example.com/v1/acceptance")
    """

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("acceptance_url_must_be_http_or_https")
    if parsed.path != "/v1/acceptance" or parsed.query or parsed.fragment:
        raise ValueError("acceptance_url_must_end_exactly_with_/v1/acceptance")


class _NoRedirect(HTTPRedirectHandler):
    """Never forward a Bearer token to a redirected URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """
        Rechaza redirecciones para proteger la cabecera Bearer de la API.

        Args:
            req: Solicitud HTTP original.
            fp: Respuesta recibida.
            code (int): Código de redirección.
            msg (str): Texto de estado.
            headers: Cabeceras HTTP.
            newurl (str): URL de redirección propuesta.

        Returns:
            None: Evita seguir la redirección.

        Raises:
            No genera excepciones deliberadas.

        Security:
            Impide reenviar el token a un destino que la herramienta no ha validado.
        """

        return None


def main(argv: list[str] | None = None) -> int:
    """
    Valida localmente una decisión firmada y la envía a la API de aceptación.

    Args:
        argv (list[str] | None): Argumentos opcionales del CLI para pruebas; `None` usa `sys.argv`.

    Returns:
        int: 0 si la API acepta la decisión; 1 si hay fallo de red/HTTP o respuesta inesperada;
        2 si la configuración, el archivo/entrada, la firma o el contrato son inválidos.

    Raises:
        ValueError y OSError previstos se capturan y se convierten a código 2; errores de
        red habituales se convierten a código 1. Errores inesperados de bibliotecas pueden
        propagarse fuera de los casos capturados.

    Side effects:
        Lee decisión desde archivo o stdin, verifica la firma con la clave pública configurada,
        usa `CHALAMANDRA_ACCEPTANCE_URL` y `CHALAMANDRA_ACCEPTANCE_TOKEN`, envía un POST HTTPS
        con `traceparent` y `X-Trace-Id`, y emite un informe JSON en stdout/stderr. La respuesta
        explícitamente fija `webhook_delivery_confirmed=False`: aceptación por API no prueba la
        entrega posterior del webhook. Timeout CLI permitido: >0 y <=60 segundos.

    Security:
        No imprime el token ni sigue redirecciones. La URL debe terminar exactamente en
        `/v1/acceptance`; la API también debe validar autorización y firma.

    Example:
        # Decisión firmada en decision.json y token provisionado en el entorno:
        # python -m verifier.submit_acceptance --input decision.json --timeout 10
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.getenv("CHALAMANDRA_ACCEPTANCE_URL", ""),
        help="Agents API /v1/acceptance URL (or CHALAMANDRA_ACCEPTANCE_URL)",
    )
    parser.add_argument(
        "--input", default="-", help="Signed decision JSON file; defaults to stdin"
    )
    parser.add_argument(
        "--timeout", type=float, default=10.0, help="HTTP timeout in seconds"
    )
    args = parser.parse_args(argv)

    try:
        if not args.url:
            raise ValueError("CHALAMANDRA_ACCEPTANCE_URL_missing")
        _validate_url(args.url)
        token = os.getenv("CHALAMANDRA_ACCEPTANCE_TOKEN", "").strip()
        if not token:
            raise ValueError("CHALAMANDRA_ACCEPTANCE_TOKEN_missing")
        if args.timeout <= 0 or args.timeout > 60:
            raise ValueError("timeout_must_be_between_0_and_60_seconds")

        decision = _read_decision(args.input)
        try:
            signature_valid = verify_decision(decision, decision["signature"])
        except (RuntimeError, ValueError) as exc:
            raise ValueError(
                "local_signature_verification_unavailable:" + str(exc)
            ) from exc
        if not signature_valid:
            raise ValueError("decision_signature_does_not_match_configured_public_key")

        span_id = secrets.token_hex(8)
        while span_id == "0" * 16:
            span_id = secrets.token_hex(8)
        traceparent = f"00-{decision['trace_id']}-{span_id}-01"
        payload = json.dumps(
            decision,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        request = Request(
            args.url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "traceparent": traceparent,
                "X-Trace-Id": decision["trace_id"],
            },
        )
        try:
            with build_opener(_NoRedirect()).open(
                request, timeout=args.timeout
            ) as response:
                status = response.status
                response_raw = response.read(64 * 1024 + 1)
        except HTTPError as exc:
            response_raw = exc.read(64 * 1024 + 1)
            try:
                body = json.loads(response_raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = {"error": "non_json_api_response"}
            _emit(
                {
                    "status": "api_rejected",
                    "http_status": exc.code,
                    "api_response": body,
                },
                stream=sys.stderr,
            )
            return 1
        except (URLError, TimeoutError, OSError) as exc:
            _emit(
                {"status": "submission_failed", "error_type": type(exc).__name__},
                stream=sys.stderr,
            )
            return 1

        if len(response_raw) > 64 * 1024:
            raise ValueError("api_response_too_large")
        try:
            body = json.loads(response_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("api_response_invalid_json") from exc
        if status != 201 or not isinstance(body, dict):
            _emit(
                {"status": "unexpected_api_response", "http_status": status},
                stream=sys.stderr,
            )
            return 1
        if body.get("decision_id") not in {None, decision["decision_id"]}:
            raise ValueError("api_decision_id_mismatch")
        if body.get("outcome") not in {None, decision["outcome"]}:
            raise ValueError("api_outcome_mismatch")

        _emit(
            {
                "status": "accepted_by_api",
                "http_status": status,
                "decision_id": decision["decision_id"],
                "candidate_id": decision["candidate_id"],
                "outcome": decision["outcome"],
                "trace_id": decision["trace_id"],
                "api_response": body,
                "webhook_delivery_confirmed": False,
            }
        )
        return 0
    except (ValueError, OSError) as exc:
        _emit({"status": "submission_not_sent", "error": str(exc)}, stream=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
