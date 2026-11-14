> **Alcance histórico — 2026-10-29.** Este documento registra dos ejecuciones sintéticas de aquella fecha; no describe el estado operativo actual ni demuestra producción vigente. Las referencias a tareas, estados y configuración son evidencia histórica inmutable. Para el contrato vigente, consultar `README.md`, `docs/INTEGRACION_GOVERNANCE.md` y el código actual de `api/task_contracts.py`, `api/worker_loop.py` y `api/server.py`.

# Estado Fail-Closed del sistema (2026-10-29)

## Resumen

El sistema CatedralX + Agents + API está en modo fail-closed por diseño.
No procesa decisiones sin governance verificable.

## Qué significa

Si faltan central_dimensions_bp o dynamic_constraints:

1. CathedralEvaluation detecta invalid_inputs no vacío.
2. Devuelve BLOCKED_UNCERTAINTY con reason ENTRADAS_CUANTITATIVAS_INVALIDAS_O_FALTANTES.
3. builder.py lo mapea a status BLOCKED_MISSING_INPUT.
4. El worker persiste el candidato con status blocked.

## Evidencia de producción (2026-10-29)

Task 1 (38993a20):
- status blocked
- summary DecisionResult de CatedralX con estado UNCERTAIN
- market_source synthetic_r3_threeflow_fixture
- Interpretación: CatedralX produjo UNCERTAIN porque faltaban dimensiones.
  El worker respetó el veredicto y bloqueó.

Task 2 (6ba41ddf):
- status blocked
- summary CatedralX no produjo un resultado elegible: stale_observation
- decision_result null
- result_id null
- Interpretación: CatedralX rechazó la entrada antes de evaluar (snapshot viejo).
  El worker bloqueó sin fabricar result_id ni decision_result.

## Por qué NO es un bug

- Aceptar sin governance: bloquea. Correcto.
- Respetar veredicto del motor: worker bloquea. Correcto.
- No fabricar IDs ni resultados: null cuando no hay decisión. Correcto.
- Rechazar datos viejos: stale_observation. Correcto.

## Cómo habilitar ACCEPTED

1. Producto/analytics envía central_dimensions_bp en POST /v1/request.
2. dynamic_constraints incluye growth_allowed true e issues vacío.
3. CatedralX evalúa con governance real.
4. Si score_bp mayor o igual a 7000 y gates pasan: ACCEPTED.
5. Verifier firma la decisión con Ed25519.
6. API persiste y distribuye.

Ver docs/INTEGRACION_GOBERNANCE.md para el contrato completo.

## Referencias

- elite/REASON/multicriteria.py: regla central fail-closed
- elite/REASON/orchestrator.py: coordinación con central_dimensions_bp
- elite/contracts/builder.py: mapeo gate a status
- api/worker_loop.py: ejecución del worker
- verifier/independent_acceptance_verifier.py: firma Ed25519
