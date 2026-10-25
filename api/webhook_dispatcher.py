"""Despachador de Webhooks para Distribution Flow (Fase 4)."""
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from api import subscription_store

def dispatch_event(event_type: str, result_id: str, trace_id: str) -> int:
    """Envía el evento a todas las suscripciones activas que lo soliciten."""
    subs = subscription_store.list_subscriptions()
    active_subs = [s for s in subs if s.get("status") == "enabled" and event_type in s.get("event_types", [])]
    
    if not active_subs:
        return 0
        
    payload = {
        "event_id": trace_id, # Usamos el trace_id como event_id para simplificar
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "result_id": result_id,
        "trace_id": trace_id,
        "result_url": f"/v1/results/{result_id}"
    }
    
    data = json.dumps(payload).encode("utf-8")
    success_count = 0
    
    for sub in active_subs:
        url = sub["endpoint_url"]
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "Chalamandra-Webhook/1.0"},
            method="POST"
        )
        try:
            # Hacemos la petición con un timeout de 5 segundos
            with urllib.request.urlopen(req, timeout=5) as resp:
                if 200 <= resp.status < 300:
                    success_count += 1
        except urllib.error.URLError as e:
            # En producción, aquí se registraría el fallo para reintentos
            print(f"Fallo al enviar webhook a {url}: {e}")
            
    return success_count
