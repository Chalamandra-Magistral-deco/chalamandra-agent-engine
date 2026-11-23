# Integración de Gobernanza

> **Estado reconciliado — 2026-11-23.** Agents valida y transporta `dynamic_constraints` y `central_dimensions_bp`; no los produce. En los repositorios inspeccionados no se ha localizado un productor conectado que genere las cinco puntuaciones de `central_dimensions_bp` con datos reales. Tampoco se ha demostrado una prueba E2E vigente con entradas reales.
>
> **Distinciones:** contrato válido ≠ procedencia demostrada ≠ datos disponibles ≠ evaluación favorable ≠ aceptación independiente ≠ operación ejecutada.
>
> El flujo descrito aquí es `catedralx_economic_evaluation`. No intercambiarlo con `catedralx_opportunity_evaluation` ni con `catedralx_economic_proposal_review`.

## 1. Separación exacta de entradas

### `central_dimensions_bp` — cinco puntuaciones enteras

| Campo | Escala / regla | Uso |
|---|---:|---|
| `viability_bp` | entero [0, 10000] | Viabilidad |
| `human_value_bp` | entero [0, 10000] | Valor humano |
| `distribution_bp` | entero [0, 10000] | Distribución |
| `resilience_bp` | entero [0, 10000] | Resiliencia |
| `evidence_bp` | entero [0, 10000] | Gate de evidencia; mínimo 8500 |

La implementación consume `viability_bp` como entrada explícita. **No afirmar que CatedralX lo deriva automáticamente de `buy`, `sell` o `hold`.**

### `dynamic_constraints` — métricas dinámicas

Incluye, entre otras, `reinvestment_surplus`, `entropy_rate`, `entropy_time_scale`, `loop_gain`, `signal_count`, `optimal_signal_count`, `exposure`, `max_exposure`, `resilience` y `minimum_resilience`.

`reinvestment_surplus` pertenece a `dynamic_constraints`, **no** a `central_dimensions_bp`. CatedralX evalúa estas restricciones en su motor dinámico; Agents solo transporta los valores recibidos.

## 2. Fórmula y gates

El motor multicriterio define:
`score_bp = (3500 * viability_bp + 2500 * resilience_bp + 2000 * human_value_bp + 2000 * distribution_bp) / 10000`

- Umbral de score: 7000 puntos básicos.
- Gate de evidencia: 8500 puntos básicos.
- Gate de reinversión: superávit válido y no negativo según las reglas exactas del evaluador; la ausencia o invalidez bloquea.
- Los gates duros prevalecen sobre el score ponderado.

Fuente ejecutable: [elite/REASON/multicriteria.py](https://github.com/Chalamandra-Magistral-deco/CatedralX/blob/main/elite/REASON/multicriteria.py).

## 3. Qué valida el contrato y qué no

En Agents, `api/task_contracts.py`:
- valida que `central_dimensions_bp` sea objeto;
- restringe sus claves a las cinco canónicas;
- valida que cada puntuación sea entero entre 0 y 10000;
- valida tipos y rangos estructurales de las métricas dinámicas;
- valida por separado la observación de mercado y su atestación criptográfica.

**Límite importante:** la atestación de mercado no acredita por sí misma el origen de las cinco puntuaciones de gobernanza. La validación estructural de `central_dimensions_bp` tampoco demuestra procedencia, método, periodo, frescura o calidad semántica de cada puntuación. El contrato actual inspeccionado no impone un sobre de evidencia/procedencia por dimensión.

La procedencia por dimensión es un requisito de diseño pendiente: propietario, fuente, periodo/grano, fórmula/versionado, timestamp de observación, frescura, unidad/escala, evidencia referenciada y digest verificable. Debe añadirse mediante un contrato versionado y pruebas; no se debe fingir que ya está validado.

## 4. Cadena observada en código

1. El cliente upstream debe enviar `central_dimensions_bp` y `dynamic_constraints` en `POST /v1/request`.
2. Agents valida su forma y transporta ambos objetos sin calcularlos.
3. El worker escribe `CENTRAL_DIMENSIONS_BP` y `DYNAMIC_CONSTRAINTS` en el snapshot de CatedralX.
4. `elite/PERCEIVE/percepcion.py` los lee y valida las claves permitidas.
5. `elite/REASON/orchestrator.py` construye `SensoryInput` y pasa las entradas a los evaluadores.
6. CatedralX produce su resultado de evaluación; Agents lo conserva como candidato y la aceptación independiente es otra etapa.

Fuentes: [Agents task_contracts.py](https://github.com/Chalamandra-Magistral-deco/chalamandra-agent-engine/blob/main/api/task_contracts.py), [Agents worker_loop.py](https://github.com/Chalamandra-Magistral-deco/chalamandra-agent-engine/blob/main/api/worker_loop.py), [CatedralX percepcion.py](https://github.com/Chalamandra-Magistral-deco/CatedralX/blob/main/elite/PERCEIVE/percepcion.py), [CatedralX orchestrator.py](https://github.com/Chalamandra-Magistral-deco/CatedralX/blob/main/elite/REASON/orchestrator.py).

## 5. Estado de productor: resultado de búsqueda

Se buscó `central_dimensions_bp`, `CENTRAL_DIMENSIONS_BP`, `human_value_bp`, `reinvestment_surplus` y `fex_report_provider` en los repositorios accesibles de la organización y se revisaron repositorios de producto/analytics candidatos.

Hallazgo:
- `central_dimensions_bp` aparece como campo de contrato, transporte, consumo y fixtures de pruebas.
- No se localizó un productor de las cinco puntuaciones que esté conectado al flujo de Agents.
- El evaluador de `dynamic_constraints` sí existe en CatedralX, pero eso no demuestra que un productor upstream le entregue métricas empresariales reales.
- `fex_report_provider` es un proveedor explícito de evidencia FEX para la barrera de ejecución/revisión; no es evidencia de que exista el productor de `central_dimensions_bp`.
- Los repositorios de producto revisados describen aplicaciones y flujos propios, pero no se ha probado que alimenten este contrato.

Esta conclusión está limitada a los repositorios y ramas inspeccionados. No equivale a demostrar que no exista un proceso externo, base de datos o servicio no conectado a GitHub.

## 6. Evidencia de pruebas

Los tests de Agents y CatedralX usan fixtures sintéticos para comprobar validación, transporte, bloqueos y comportamiento determinista. Por ejemplo, `tests/test_worker_loop.py` construye puntuaciones fijas de 9000 y una fuente `synthetic_verified_test_feed`.

Esto prueba el comportamiento del test con esos valores; no demuestra que una fuente de negocio real produzca las puntuaciones ni que exista una integración operativa vigente.

## 7. Bloqueos y próximos criterios

| Prioridad | Trabajo | Criterio verificable de cierre |
|---|---|---|
| P0 | Nombrar propietario/productor de las cinco puntuaciones | Sistema y responsable identificados; código/ruta de producción localizada |
| P0 | Contrato de procedencia por dimensión | Esquema versionado, fuente, fórmula, grano, timestamp, frescura y digest |
| P0 | Separar productor de métricas dinámicas | Origen verificable de cada campo de `dynamic_constraints` |
| P0 | E2E con datos controlados y trazables | API → TaskBus → worker → snapshot → CatedralX → DecisionResult; evidencia guardada y reproducible |
| P1 | Pruebas negativas y manipulación | Campo ausente, obsoleto, fuera de rango, procedencia falsa, digest inválido y replay |
| P1 | Aceptación y distribución | Verificar `decision_id`, firma, permisos y entrega condicionada |

Para el E2E, usar datos controlados con procedencia documentada antes de usar datos de negocio reales. Los datos sintéticos deben etiquetarse como sintéticos y nunca pasar como evidencia de producción.

## 8. No hacer

- No calcular estas puntuaciones dentro de Agents.
- No asumir que `viability_bp` se deriva de la etiqueta de decisión.
- No mover `reinvestment_surplus` a `central_dimensions_bp`.
- No convertir un label de fuente o una firma de precios en prueba de procedencia de las puntuaciones de gobernanza.
- No usar constantes, ceros o valores por perfil para evitar el fail-closed.
- No tratar `ACCEPTED` de CatedralX como aceptación independiente de Agents.
- No declarar producción operativa basándose en fixtures o archivos presentes.

## 9. Referencia de arquitectura

Consultar [ARQUITECTURA_CANONICA_CATEDRALX_AGENTS.md](./ARQUITECTURA_CANONICA_CATEDRALX_AGENTS.md).
