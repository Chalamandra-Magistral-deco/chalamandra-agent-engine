"""Persistencia y política SSRF para suscripciones webhook."""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from api.safe_http import ResolvedWebhookTarget
from api.jsonl_index import JsonlIndex


def _state_dir() -> Path:
    return Path.home() / ".local" / "state" / "chalamandra" / "agents"


def _subs_file() -> Path:
    return _state_dir() / "subscriptions.jsonl"


def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _read_all(path: Path) -> list[dict]:
    """Fail closed when the append-only subscription ledger is malformed."""
    if not path.exists():
        return []
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"subscriptions.jsonl inválido en línea {line_number}"
                ) from exc
            if not isinstance(record, dict):
                raise ValueError(
                    f"Registro de suscripción no-objeto en línea {line_number}"
                )
            records.append(record)
    return records


def resolve_safe_url(url: str) -> ResolvedWebhookTarget | None:
    """Resolve once and return a destination that can be connected to by pinned IP.

    Public destinations must use HTTPS and resolve exclusively to globally routable
    addresses. Private/loopback destinations are allowed only for exact hostnames
    explicitly listed in CHALAMANDRA_WEBHOOK_PRIVATE_HOST_ALLOWLIST. The legacy
    CHALAMANDRA_SSRF_ALLOW_LOCALHOST switch is intentionally ignored.
    """
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            return None
        if parsed.username or parsed.password or parsed.fragment:
            return None

        hostname = parsed.hostname.casefold().rstrip(".")
        if not hostname or "%" in hostname or any(ch.isspace() for ch in hostname):
            return None
        if hostname in {
            "metadata",
            "metadata.google.internal",
            "metadata.azure.internal",
            "instance-data.ec2.internal",
        } or hostname.endswith(".metadata.google.internal"):
            return None

        explicit_port = parsed.port
        port = explicit_port if explicit_port is not None else (
            443 if parsed.scheme == "https" else 80
        )
        if not 1 <= port <= 65535:
            return None

        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address]
        try:
            direct_ip = ipaddress.ip_address(hostname)
            addresses = {direct_ip}
        except ValueError:
            infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            addresses = set()
            for item in infos:
                address_text = item[4][0]
                if not isinstance(address_text, str):
                    return None
                addresses.add(ipaddress.ip_address(address_text.split("%", 1)[0]))

        if not addresses:
            return None
        if any(
            address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
            for address in addresses
        ):
            return None

        private_allowlist = {
            item.strip().casefold().rstrip(".")
            for item in os.environ.get(
                "CHALAMANDRA_WEBHOOK_PRIVATE_HOST_ALLOWLIST", ""
            ).split(",")
            if item.strip()
        }
        all_private = all(
            address.is_private or address.is_loopback for address in addresses
        )
        if all_private:
            if hostname not in private_allowlist:
                return None
        elif not all(address.is_global for address in addresses):
            return None
        elif parsed.scheme != "https":
            return None

        request_target = parsed.path or "/"
        if parsed.query:
            request_target += "?" + parsed.query

        return ResolvedWebhookTarget(
            scheme=parsed.scheme,
            hostname=hostname,
            port=port,
            request_target=request_target,
            addresses=tuple(sorted(str(address) for address in addresses)),
        )
    except (OSError, ValueError, TypeError):
        return None


def is_safe_url(url: str) -> bool:
    """Compatibility predicate; delivery code must use resolve_safe_url()."""
    return resolve_safe_url(url) is not None


def create_subscription(endpoint_url: str, event_types: list[str]) -> dict:
    if resolve_safe_url(endpoint_url) is None:
        raise ValueError(
            "URL inválida o bloqueada por política SSRF; se requiere HTTPS público "
            "o un host privado explícitamente autorizado"
        )

    allowed_event_types = {"result.accepted"}
    if (
        not event_types
        or len(event_types) > 8
        or len(set(event_types)) != len(event_types)
        or not all(item in allowed_event_types for item in event_types)
    ):
        raise ValueError("event_types inválidos")

    record = {
        "subscription_id": str(uuid.uuid4()),
        "endpoint_url": endpoint_url,
        "event_types": event_types,
        "status": "enabled",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    store = JsonlIndex(_subs_file(), ("subscription_id",))
    with store.locked() as db:
        store.append(db, record)
    return record


def list_subscriptions() -> list[dict]:
    """Return the latest append-only state per subscription using the side index."""
    store = JsonlIndex(_subs_file(), ("subscription_id",))
    with store.locked() as db:
        records = store.latest_by_key(db, "subscription_id")
    for record in records:
        subscription_id = record.get("subscription_id")
        if not isinstance(subscription_id, str) or not subscription_id:
            raise ValueError("Registro de suscripción sin subscription_id válido")
    return records


def disable_subscription(sub_id: str) -> bool:
    if not isinstance(sub_id, str) or not sub_id.strip():
        return False
    store = JsonlIndex(_subs_file(), ("subscription_id",))
    with store.locked() as db:
        matches = store.find(db, "subscription_id", sub_id)
        if not matches:
            return False
        # Preserve the complete public subscription record in each append-only
        # state transition. latest_by_key() returns only the newest row.
        record = dict(matches[-1])
        record["subscription_id"] = sub_id
        record["status"] = "disabled"
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        store.append(db, record)
    return True
