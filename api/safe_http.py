"""HTTP delivery pinned to a previously validated DNS result."""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ResolvedWebhookTarget:
    scheme: str
    hostname: str
    port: int
    request_target: str
    addresses: tuple[str, ...]


class PinnedWebhookError(OSError):
    """A webhook connection to the validated destination could not be completed."""


def _connect_to_ip(address: str, port: int, timeout: float | None) -> socket.socket:
    """Open a TCP socket to a numeric IP without calling getaddrinfo again."""
    ip = ipaddress.ip_address(address)
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    stream = socket.socket(family, socket.SOCK_STREAM)
    try:
        stream.settimeout(timeout)
        sockaddr = (str(ip), port, 0, 0) if ip.version == 6 else (str(ip), port)
        stream.connect(sockaddr)
        return stream
    except BaseException:
        stream.close()
        raise


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, hostname: str, address: str, port: int, timeout: float) -> None:
        super().__init__(hostname, port=port, timeout=timeout)
        self._pinned_address = address

    def connect(self) -> None:
        self.sock = _connect_to_ip(
            self._pinned_address,
            self.port,
            self.timeout,
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        hostname: str,
        address: str,
        port: int,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(hostname, port=port, timeout=timeout, context=context)
        self._pinned_address = address
        self._ssl_context = context

    def connect(self) -> None:
        raw_socket = _connect_to_ip(
            self._pinned_address,
            self.port,
            self.timeout,
        )
        try:
            # Keep the original hostname for certificate validation and TLS SNI,
            # while the TCP socket connects only to the already-validated IP.
            self.sock = self._ssl_context.wrap_socket(
                raw_socket,
                server_hostname=self.host,
            )
        except Exception:
            raw_socket.close()
            raise


def post_json(
    target: ResolvedWebhookTarget,
    *,
    data: bytes,
    headers: Mapping[str, str],
    timeout: float = 5.0,
) -> int:
    """POST to an immutable DNS resolution; never perform a second hostname lookup."""
    failures: list[str] = []
    context = ssl.create_default_context() if target.scheme == "https" else None

    for address in target.addresses:
        connection: http.client.HTTPConnection
        if target.scheme == "https":
            assert context is not None
            connection = _PinnedHTTPSConnection(
                target.hostname, address, target.port, timeout, context
            )
        else:
            connection = _PinnedHTTPConnection(
                target.hostname, address, target.port, timeout
            )

        try:
            connection.request(
                "POST",
                target.request_target,
                body=data,
                headers=dict(headers),
            )
            response = connection.getresponse()
            status = response.status
            # Drain only a bounded amount; the connection is closed regardless.
            response.read(64 * 1024)
            return status
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            failures.append(type(exc).__name__)
        finally:
            connection.close()

    detail = ",".join(failures[-3:]) or "no_validated_addresses"
    raise PinnedWebhookError(f"pinned_webhook_delivery_failed:{detail}")
