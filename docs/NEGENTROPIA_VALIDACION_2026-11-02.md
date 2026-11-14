> **Registro histórico de validación — 2026-11-02.** Los hashes, ramas, conteos, contenedores y estados descritos corresponden exclusivamente a esa ejecución local. No representan HEAD de main ni estado operativo actual al 2026-11-14. La sección de pendientes no debe tratarse como una lista vigente sin volver a comprobar el código y los workflows actuales. Se conserva como evidencia histórica, no como runbook actual.

# Neguentropía Elite — evidencia de validación

**Fecha:** 2026-11-02
**Entorno:** chalamandra-vm, Ubuntu 24.04, Docker
**Modo:** validación local y E2E aislada; sin push, PR ni despliegue.

## Versiones probadas

| Componente | Referencia |
|---|---|
| CatedralX | ec3d83d2626b51d03936d3eb1efa209aafba3ae3 (main) |
| Agents | c54ff2e (wip/reconciled-candidate-state-20261028) |
| API/Worker | imágenes locales de validación; no desplegadas |
| Ejecución | CHALAMANDRA_API_ENABLE_MODEL_CALLS=false |

La copia local de CatedralX está dos commits por delante de origin/main. Agents conserva su rama candidata existente. No se fusionó el commit de respaldo de main: una simulación reveló diez conflictos funcionales. Se rescataron selectivamente el cliente de envío de aceptación y sus pruebas; se mantuvieron los contratos y el despacho de la candidata.

## Cambios locales realizados

1. feat(verifier): add signed acceptance API submitter (528016d): incorpora un cliente que verifica localmente una decisión firmada y la envía a /v1/acceptance. No contiene ni carga la semilla privada.
2. fix(verifier): restrict plaintext acceptance URLs (6c82a50): restringe HTTP sin TLS a loopback o red privada con opt-in explícito; destinos públicos requieren HTTPS.
3. fix(verifier): validate DNS sockaddr types (c54ff2e): añade guardas de tipo a las dos rutas de resolución DNS; corrige dos errores Mypy sin relajar la política de destino HTTP.
4. security: declare worker non-root user (ed5193e): declara el usuario no root del worker.
5. En .github/workflows/release.yml se conserva un cambio concurrente de CI y se corrigió el bucle del smoke test para eliminar SC2034. Esa modificación permanece sin commit y no se incluye en los commits anteriores.

## Pruebas de código

| Repositorio / alcance | Resultado |
|---|---:|
| CatedralX, suite completa | 344 aprobadas |
| Agents, HEAD c54ff2e, suite completa | 226 aprobadas; 0 fallos, 0 errores, 0 omitidas |
| Agents, cliente de aceptación (focalizada) | 7 aprobadas |
| Agents, aceptación/verificador/persistencia (focalizada) | 22 aprobadas |
| Agents, distribución/webhook (filtro focalizado) | 14 aprobadas; 16 filtradas |
| Ruff, módulos Agents y verifier | Aprobado |
| Mypy, 55 archivos fuente Agents | Aprobado; 0 errores después del parche c54ff2e |
| actionlint, workflows CatedralX y Agents | Aprobado |
| git diff --check | Aprobado |

La suite completa de Agents se repitió después del arreglo DNS y pasó con 226/226.

## Construcción local de imágenes

Imágenes locales creadas con docker build --pull=false. No se publicaron ni desplegaron.

| Tag | Base de código | Usuario |
|---|---|---|
| catedralx:decox-local-validation-ec3d83d | CatedralX ec3d83d | catedralx |
| agents-api:decox-local-validation-528016d | API Agents basada en la imagen anterior | chalamandra |
| agents-worker:decox-local-validation-528016d | Worker basado en API local | chalamandra |

Las imágenes API/Worker de ensayo se construyeron antes de los commits posteriores del cliente verifier (6c82a50, c54ff2e); esos commits modifican únicamente el cliente verifier y sus pruebas, no el código copiado por los Dockerfiles API/Worker.

El worker se inició además en una prueba aislada sin red y con estado efímero: healthcheck healthy, heartbeat idle, UID/GID 1000:1000.

## Flujos E2E observados en contenedores aislados

### Normal — solicitud → API → Agents Worker → CatedralX

- Autorización con scope incorrecto: HTTP 401.
- Solicitud autorizada a /v1/request: HTTP 202.
- Repetición con la misma Idempotency-Key: mismo task_id y run_id; no duplicó la tarea.
- Worker ejecutó el CLI real de CatedralX con datos sintéticos.
- CatedralX devolvió BLOCKED_MISSING_INPUT porque faltaba evidencia/gobernanza exigida.
- El resultado quedó persistido como bloqueado y visible como pending_verification.
- El run quedó completed_with_errors; no hubo webhook.
- execution_enabled=false se mantuvo.

### Inverso — CatedralX → dispatch_agents.py → API/Task Bus → Worker

- Se escribió un ciclo sintético measure → evaluate → propose → authorize.
- El cliente oficial elite/bin/dispatch_agents.py lo despachó con JWT de servicio y traceparent.
- Repetición idempotente: mismo task_id y run_id.
- Worker procesó la propuesta autorizada sin llamadas a modelos.
- Resultado: candidato pendiente de aceptación; orders_created=false, orders_executed=false.
- Correlación W3C preservada entre solicitud, tarea, resultado y run.

### Publicación inversa — resultado → aceptación → webhook

- Distribución del candidato antes de aceptación independiente: HTTP 409 result_not_independently_accepted.
- La aceptación criptográfica y la entrega webhook están cubiertas por las pruebas automatizadas focalizadas y la suite de integración.
- No se completó un envío de aceptación vivo en el contenedor E2E. Las operaciones de firma/envío con clave de prueba fueron bloqueadas por los controles de seguridad antes de ejecutarse. No se afirma que el webhook haya sido entregado en esa corrida.

## Limpieza y conservación

- Se retiraron los tres contenedores creados para el ensayo y la red Docker dedicada.
- Se conservó el volumen agents-e2e-validation-20261102, que contiene únicamente datos sintéticos de la corrida; no se eliminó por la regla de no borrar datos persistentes sin autorización.
- Se conservaron las imágenes locales de validación.
- Los logs de ensayo quedaron en /tmp/negentropia-e2e-evidence-20261102/.
- No se eliminaron volúmenes productivos, imágenes base, ramas, historial ni archivos de trabajo.
- No se reinició ni reemplazó ningún servicio productivo.
- La API productiva continuó respondiendo status=ok, execution_enabled=false.

## Estado y pendientes

- La rama main de CatedralX sigue sin publicar sus dos commits locales.
- Agents permanece en la rama de trabajo existente; no hubo push ni PR.
- release.yml conserva una modificación concurrente sin commit; debe revisarse antes de preparar una publicación.
- El volumen sintético de prueba está preservado y requiere autorización explícita para eliminarse.
- Falta completar una aceptación firmada y entrega webhook en vivo con una identidad/credencial de verificador autorizada; no se habilitaron llamadas a modelos ni ejecución financiera.
