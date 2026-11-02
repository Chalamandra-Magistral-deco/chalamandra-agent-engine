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
    """Allow public HTTPS endpoints; test override is restricted to private test targets.

    The test override must never turn off SSRF checks globally. Public HTTP, metadata,
    link-local, multicast, unspecified, reserved, and mixed public/private DNS results
    remain blocked even when CHALAMANDRA_SSRF_ALLOW_LOCALHOST=true.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            return False
        if parsed.username or parsed.password or parsed.fragment:
            return False
        hostname = parsed.hostname.casefold().rstrip(".")
        if hostname in {
            "metadata", "metadata.google.internal", "metadata.azure.internal",
            "instance-data.ec2.internal",
        } or hostname.endswith(".metadata.google.internal"):
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address]
        try:
            direct_ip = ipaddress.ip_address(hostname)
            addresses = {direct_ip}
        except ValueError:
            infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            addresses = set()
            for item in infos:
                address_text: object = item[4][0]
                if not isinstance(address_text, str):
                    return False
                addresses.add(
                    ipaddress.ip_address(address_text.split("%", 1)[0])
                )
        if not addresses:
            return False

        # These ranges are never valid webhook destinations, including in test mode.
        if any(
            ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved
            for ip in addresses
        ):
            return False

        allow_test_local = os.environ.get("CHALAMANDRA_SSRF_ALLOW_LOCALHOST") == "true"
        private_or_loopback = all(ip.is_private or ip.is_loopback for ip in addresses)
        if private_or_loopback:
            return allow_test_local

        # Never accept mixed public/private DNS answers. Only public HTTPS is allowed
        # outside the narrowly scoped test override above.
        if not all(ip.is_global for ip in addresses):
            return False
        return parsed.scheme == "https"
    except (OSError, ValueError, TypeError):
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
