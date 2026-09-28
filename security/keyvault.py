"""
Acceso a Azure Key Vault usando Managed Identity de la VM.

Ventaja: las API keys nunca tocan el disco de la VM.
La MI (System Assigned) autentica automáticamente ante Azure.
"""

from __future__ import annotations

import os

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

_CACHE: dict[str, str] = {}


def _vault_uri() -> str:
    """URI del Key Vault. Configurable vía variable de entorno."""
    uri = os.getenv("AZURE_KEYVAULT_URI", "")
    if not uri:
        raise RuntimeError(
            "AZURE_KEYVAULT_URI no configurada. "
            "Ejemplo: https://chalamandra-kv-2026.vault.azure.net/"
        )
    return uri


def get_secret(name: str, *, use_cache: bool = True) -> str:
    """
    Devuelve el valor de un secreto de Key Vault.

    - Usa DefaultAzureCredential → en la VM usa Managed Identity.
    - Cachea en memoria para no llamar a Key Vault en cada adaptador.
    - Si falla, cae a variable de entorno con el mismo nombre (fallback dev).
    """
    if use_cache and name in _CACHE:
        return _CACHE[name]

    # Fallback: variable de entorno con el mismo nombre
    env_fallback = os.getenv(name)
    if env_fallback:
        _CACHE[name] = env_fallback
        return env_fallback

    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=_vault_uri(), credential=credential)

    secret = client.get_secret(name)
    value = secret.value
    if value is None:
        raise RuntimeError(f"Secreto '{name}' sin valor en Key Vault")

    _CACHE[name] = value
    return value


def set_secret(name: str, value: str) -> None:
    """Crea o actualiza un secreto. Requiere rol Key Vault Secrets Officer."""
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=_vault_uri(), credential=credential)
    client.set_secret(name, value)
    _CACHE.pop(name, None)


def clear_cache() -> None:
    """Limpia la caché en memoria."""
    _CACHE.clear()
