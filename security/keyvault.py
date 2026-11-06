"""
Acceso a Azure Key Vault usando Managed Identity de la VM.

Ventaja: las API keys nunca tocan el disco de la VM.
La MI (System Assigned) autentica automáticamente ante Azure.

Orden de resolución (fail-safe):
  1. Caché en memoria con TTL de 5 minutos.
  2. Key Vault vía Managed Identity (fuente de verdad).
  3. Variable de entorno del mismo nombre (fallback explícito de desarrollo).

La variable de entorno NUNCA shadowea al vault. Si el vault responde, se usa el
vault. La env var solo entra si el vault falla (sin red, sin MI, sin permisos).
"""

from __future__ import annotations

import os
import time

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

_CACHE: dict[str, tuple[str, float]] = {}
_CACHE_TTL_SECONDS = 300.0


def _vault_uri() -> str:
    """URI del Key Vault. Configurable vía variable de entorno."""
    uri = os.getenv("AZURE_KEYVAULT_URI", "")
    if not uri:
        raise RuntimeError(
            "AZURE_KEYVAULT_URI no configurada. "
            "Ejemplo: https://chalamandra-kv-2026.vault.azure.net/"
        )
    return uri


def _cache_get(name: str) -> str | None:
    entry = _CACHE.get(name)
    if entry is None:
        return None
    value, expires_at = entry
    if time.monotonic() > expires_at:
        _CACHE.pop(name, None)
        return None
    return value


def _cache_set(name: str, value: str) -> None:
    _CACHE[name] = (value, time.monotonic() + _CACHE_TTL_SECONDS)


def get_secret(name: str, *, use_cache: bool = True) -> str:
    """
    Devuelve el valor de un secreto.

    Orden de resolución (fail-safe):
      1. Caché en memoria (si `use_cache=True`).
      2. Key Vault vía Managed Identity (fuente de verdad).
      3. Variable de entorno con el mismo nombre (fallback explícito de dev).

    Raises:
        RuntimeError: Si ninguna de las tres fuentes lo proporciona.
    """
    if use_cache:
        cached = _cache_get(name)
        if cached is not None:
            return cached

    # 1. Key Vault (fuente de verdad)
    try:
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=_vault_uri(), credential=credential)
        secret = client.get_secret(name)
        value = secret.value
        if value is None:
            raise RuntimeError(f"Secreto '{name}' sin valor en Key Vault")
        _cache_set(name, value)
        return value
    except Exception as vault_error:
        # 2. Fallback explícito: env var solo si el vault no está disponible.
        env_fallback = os.getenv(name)
        if env_fallback:
            _cache_set(name, env_fallback)
            return env_fallback
        raise RuntimeError(
            f"No se pudo resolver el secreto '{name}' ni desde Key Vault ni desde "
            f"variable de entorno. Error original: {vault_error}"
        ) from vault_error


def set_secret(name: str, value: str) -> None:
    """Crea o actualiza un secreto. Requiere rol Key Vault Secrets Officer."""
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=_vault_uri(), credential=credential)
    client.set_secret(name, value)
    _CACHE.pop(name, None)


def clear_cache() -> None:
    """Limpia la caché en memoria."""
    _CACHE.clear()
