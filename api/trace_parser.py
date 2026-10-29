"""Valida encabezados W3C Trace Context para correlación distribuida."""

import re
from typing import Optional, Dict

def parse_traceparent(traceparent: Optional[str]) -> Optional[Dict[str, str]]:
    """
    Valida y separa un encabezado W3C `traceparent` de contexto de traza.

    Args:
        traceparent (Optional[str]): Valor esperado con formato
            `version-trace-id-parent-id-flags`, usando hexadecimal minúsculo.

    Returns:
        Optional[Dict[str, str]]: Diccionario con `version`, `trace_id`, `parent_id`
        y `flags`; devuelve `None` ante una entrada ausente o inválida, o cuando los
        identificadores trace/parent son todos ceros o la versión es `ff`.

    Raises:
        No lanza excepciones por valores de entrada inválidos; devuelve `None`.

    Side effects:
        Ninguno; no crea ni persiste una traza.

    Example:
        context = parse_traceparent("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
    """
    if not traceparent:
        return None
    
    # Regex estricta W3C Trace Context Level 2: version(2) - trace-id(32) - parent-id(16) - flags(2)
    # Solo minúsculas hexadecimales
    pattern = r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$"
    match = re.match(pattern, traceparent.strip())
    
    if not match:
        return None
        
    version, trace_id, parent_id, flags = match.groups()
    
    # Reglas de invalidación explícitas W3C
    if trace_id == "0" * 32:
        return None
    if parent_id == "0" * 16:
        return None
    # Para version 00 se admite el bit sampled (flags=01) y se ignoran bits reservados.
    if version == "ff":
        return None
        
    return {
        "version": version,
        "trace_id": trace_id,
        "parent_id": parent_id,
        "flags": flags
    }
