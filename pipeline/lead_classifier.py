"""Clasificador determinista de leads del formulario de contacto.

No usa LLM ni red. Recibe datos del formulario, clasifica por reglas
explícitas y appendea a un JSONL local. No envía respuestas al cliente.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID_TIPOS = {
    "mapa_srap", "decodificar", "taller_srap", "arquitectura_sistemas",
    "disenar", "metodologia", "recurso", "otro",
}

PROPUESTA_POR_TIPO = {
    "mapa_srap": "Mapa SRAP",
    "decodificar": "Consultoría de decodificación",
    "taller_srap": "Talleres SRAP y 6 Sombreros",
    "arquitectura_sistemas": "Arquitectura de sistemas y marca",
    "disenar": "Consultoría de decodificación",
    "metodologia": "Consultoría de decodificación",
    "recurso": "Recursos y materiales",
    "otro": "Revisar manualmente",
}

URGENCIA_ALTA = (
    "urgente", "urgencia", "cuanto antes", "lo antes posible",
    "esta semana", "hoy", "manana", "mañana", "deadline", "fecha limite",
    "fecha límite", "presupuesto", "cotizacion", "cotización",
    "cuanto cuesta", "cuánto cuesta", "precio", "tarifa", "inversion", "inversión",
)

URGENCIA_MEDIA = (
    "pronto", "proximamente", "próximamente", "este mes",
    "quiero empezar",
)

SENALES_PROPUESTA = {
    "Mapa SRAP": ("mapa", "srap", "mapa srap"),
    "Consultoría de decodificación": ("consultor", "consult", "decodif", "resolver"),
    "Talleres SRAP y 6 Sombreros": ("taller", "curso", "formacion", "formación", "sombreros"),
    "Arquitectura de sistemas y marca": ("sistema", "arquitectura", "marca", "web", "app", "plataforma"),
    "Recursos y materiales": ("recurso", "kit", "plantilla", "material", "descargar"),
}

MAX_MESSAGE_CHARS = 10_000
MAX_FIELD_CHARS = 500


def _leads_path() -> Path:
    configured = os.getenv("CHALAMANDRA_LEADS_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "state" / "chalamandra" / "leads.jsonl"


def _norm(text: str) -> str:
    return text.casefold()


def _validate_email(email: str) -> str:
    email = (email or "").strip()
    if not email or len(email) > 200:
        raise ValueError("email inválido")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValueError("email inválido")
    return email


def _clean_field(name: str, value: Any, *, required: bool, max_len: int = MAX_FIELD_CHARS) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        value = str(value)
    value = value.replace("\x00", "").strip()
    if required and not value:
        raise ValueError(f"{name} es obligatorio")
    if len(value) > max_len:
        raise ValueError(f"{name} excede {max_len} caracteres")
    return value


def _detect_urgency(text: str) -> tuple[str, list[str]]:
    text_norm = _norm(text)
    senales = []
    for kw in URGENCIA_ALTA:
        if kw in text_norm:
            senales.append(f"urgencia_alta:{kw}")
    if senales:
        return "alta", senales
    for kw in URGENCIA_MEDIA:
        if kw in text_norm:
            senales.append(f"urgencia_media:{kw}")
    if senales:
        return "media", senales
    return "baja", []


def _detect_propuesta(message: str, tipo: str) -> tuple[str, float, list[str]]:
    base = PROPUESTA_POR_TIPO.get(tipo, "Revisar manualmente")
    base_conf = 0.6 if tipo and tipo != "otro" else 0.0

    msg_norm = _norm(message)
    detected: list[str] = []
    for propuesta, keywords in SENALES_PROPUESTA.items():
        for kw in keywords:
            if kw in msg_norm:
                detected.append(propuesta)
                break

    if not detected:
        return base, base_conf, [f"tipo:{tipo}"] if tipo else []

    if base in detected:
        detected.remove(base)
        return base, min(0.95, base_conf + 0.3), [f"tipo:{tipo}"] + [f"kw:{d}" for d in detected]

    propuesta = detected[0]
    return propuesta, 0.7 if base_conf > 0 else 0.5, [f"tipo:{tipo}"] + [f"kw:{d}" for d in detected]


def _append_lead(record: dict[str, Any]) -> Path:
    path = _leads_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def classify_lead(
    *,
    lead_name: str,
    email: str,
    tipo: str,
    message: str,
    utm_source: str = "",
    utm_medium: str = "",
    utm_campaign: str = "",
) -> dict[str, Any]:
    """Clasifica un lead. Determinista, sin LLM, sin red, sin email."""
    try:
        name_clean = _clean_field("lead_name", lead_name, required=True)
        email_clean = _validate_email(email)
        tipo_clean = _clean_field("tipo", tipo, required=True, max_len=50)
        if tipo_clean not in VALID_TIPOS:
            raise ValueError(f"tipo inválido: {tipo_clean}")
        message_clean = _clean_field("message", message, required=True, max_len=MAX_MESSAGE_CHARS)

        urgencia, senales_urg = _detect_urgency(message_clean + " " + tipo_clean)
        propuesta, confidence, senales_prop = _detect_propuesta(message_clean, tipo_clean)
        senales = senales_urg + senales_prop

        lead_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()

        record = {
            "lead_id": lead_id,
            "timestamp": now,
            "name": name_clean,
            "email": email_clean,
            "tipo": tipo_clean,
            "message": message_clean,
            "utm": {
                "source": (utm_source or "")[:100],
                "medium": (utm_medium or "")[:100],
                "campaign": (utm_campaign or "")[:100],
            },
            "classification": {
                "urgencia": urgencia,
                "propuesta_sugerida": propuesta,
                "confidence": round(confidence, 2),
                "senales": senales,
            },
        }

        path = _append_lead(record)
        return {
            "status": "ok",
            "lead_id": lead_id,
            "timestamp": now,
            "classification": record["classification"],
            "stored_path": str(path),
            "response_sent": False,
            "orders_created": False,
        }
    except (ValueError, TypeError) as e:
        return {"status": "error", "error": str(e), "response_sent": False}
