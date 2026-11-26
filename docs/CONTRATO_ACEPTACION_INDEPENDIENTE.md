# Contrato de aceptación independiente — Agents

**Versión:** 1.0  
**Estado:** contrato formal publicado; integración automática del schema pendiente de pruebas.

## Autoridad

- El productor crea un candidato; no puede aceptarlo a sí mismo.
- El proceso `verifier/independent_acceptance_verifier.py` valida la decisión y es el único componente que firma.
- La API conserva únicamente la clave pública y verifica Ed25519.
- La aceptación no convierte un `DecisionResult` en una orden financiera ni autoriza por sí sola una operación externa.

## Esquema canónico

La definición machine-readable está en:

`contracts/acceptance/acceptance-decision.schema.json`

Se aplica al objeto completo firmado: diez campos canónicos más `signature`. La firma Ed25519 cubre exactamente los diez campos canónicos en el orden/serialización implementados por `api/crypto_signer.py`; `signature` queda fuera del payload firmado.

La canonicalización actual es JSON compacto ordenado por clave, UTF-8, `ensure_ascii=false` y `allow_nan=false`. No se declara conformidad RFC 8785.

## Reglas

- Se requieren exactamente los diez campos canónicos y `signature`; no se permiten propiedades adicionales.
- `outcome` es `accepted` o `rejected`.
- `evidence_digest` es SHA-256 hexadecimal minúsculo de 64 caracteres.
- `trace_id` es un W3C trace-id hexadecimal minúsculo de 32 caracteres y no puede ser todo ceros.
- `decided_at` debe incluir zona horaria.
- `signature` contiene 64 bytes Ed25519 codificados como 128 caracteres hexadecimales minúsculos.
- Una aceptación requiere al menos un criterio comprobado y listas `reasons` y `missing_evidence` vacías.
- Un rechazo requiere al menos una razón o una evidencia faltante.
- El store vuelve a comprobar firma, identidad del verificador, candidato, trace_id y digest contra la evidencia persistida.

El schema expresa restricciones estructurales. Las comprobaciones criptográficas, la igualdad con el candidato persistido, el digest recalculado y la identidad configurada siguen siendo validaciones de runtime y no pueden ser sustituidas por JSON Schema.

## Fixtures

- `tests/fixtures/acceptance/accepted.json`
- `tests/fixtures/acceptance/rejected.json`

Las firmas de los fixtures son valores sintéticos de forma correcta; no representan firmas criptográficas verificables ni autorizan distribución.

## Alcance de esta entrega

Esta entrega publica el contrato y ejemplos compartidos sin cambiar el flujo de ejecución, los secretos, el store, la API ni la distribución. El siguiente paso técnico es conectar un validador Draft 2020-12 al CI y comprobar que el esquema y `validate_decision` aceptan/rechazan los mismos casos. Hasta entonces, el schema es contrato versionado, no una validación runtime ya integrada.
