import re
from typing import Optional, Dict

def parse_traceparent(traceparent: Optional[str]) -> Optional[Dict[str, str]]:
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
    if version == "00" and flags != "00":
        return None
    if version == "ff":
        return None
        
    return {
        "version": version,
        "trace_id": trace_id,
        "parent_id": parent_id,
        "flags": flags
    }
