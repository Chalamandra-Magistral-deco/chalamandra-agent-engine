"""Store y validación SSRF para Suscripciones (Fase 4)."""
import ipaddress
import json
import os
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

def _state_dir() -> Path:
    return Path.home() / ".local" / "state" / "chalamandra" / "agents"

def _subs_file() -> Path:
    return _state_dir() / "subscriptions.jsonl"

def _append(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def _read_all(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records

def is_safe_url(url: str) -> bool:
    """Bloquea loopback, redes privadas, link-local y metadata endpoints."""
    if os.environ.get("CHALAMANDRA_SSRF_ALLOW_LOCALHOST") == "true":
        return True  # Solo para tests locales
        
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return False  # Solo HTTPS en producción
            
        hostname = parsed.hostname
        if not hostname:
            return False
            
        # Bloquear nombres obvios
        if hostname in ("localhost", "127.0.0.1", "::1", "169.254.169.254"):
            return False
            
        # Resolver DNS a IP y validar
        ip = socket.gethostbyname(hostname)
        ip_obj = ipaddress.ip_address(ip)
        
        # Bloquear IPs privadas o reservadas
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast:
            return False
            
        return True
    except Exception:
        return False

def create_subscription(endpoint_url: str, event_types: list[str]) -> dict:
    if not is_safe_url(endpoint_url):
        raise ValueError("URL inválida o bloqueada por política SSRF (se requiere HTTPS público)")
    
    if not event_types or not all(et in ("result.accepted", "result.blocked") for et in event_types):
        raise ValueError("event_types inválidos")

    sub_id = str(uuid.uuid4())
    record = {
        "subscription_id": sub_id,
        "endpoint_url": endpoint_url,
        "event_types": event_types,
        "status": "enabled",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    _append(_subs_file(), record)
    return record

def list_subscriptions() -> list[dict]:
    """Retorna el estado más reciente de cada suscripción."""
    latest = {}
    for rec in _read_all(_subs_file()):
        latest[rec["subscription_id"]] = rec
    return list(latest.values())

def disable_subscription(sub_id: str) -> bool:
    subs = list_subscriptions()
    target = next((s for s in subs if s["subscription_id"] == sub_id), None)
    if not target:
        return False
    
    _append(_subs_file(), {
        "subscription_id": sub_id,
        "status": "disabled",
        "updated_at": datetime.now(timezone.utc).isoformat()
    })
    return True
