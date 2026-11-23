# Arquitectura canónica CatedralX ↔ Agents

> **Estado:** lectura estática de repositorios; no certifica runtime ni producción. Fecha: 2026-11-23.  
> **Alcance:** documentación solamente. No se modificó código funcional ni se ejecutaron tests, CI o despliegues.

## 1. Propósito y límites

- **CatedralX:** motor experimental de razonamiento y análisis financiero; produce resultados estructurados dentro de ese dominio. No es un motor universal ni un sistema de trading de producción.
- **Agents:** API HTTP, contratos, TaskBus, worker, adaptadores y almacenes que coordinan tareas y conservan candidatos.
- **Membrana contractual:** patrón de controles distribuido en API, contratos, TaskBus, worker, almacenes y verificador; no es un microservicio independiente.
- **Verificador independiente:** comprueba evidencia y criterios del candidato antes de registrar aceptación.

Las etiquetas analíticas `buy`, `sell` y `hold` no son órdenes ni autorización de ejecución.

## 2. Responsabilidades

| Componente | Responsabilidad | Límite |
|---|---|---|
| CatedralX | Evaluación y `DecisionResult` | No autoriza ni ejecuta operaciones externas por sí solo |
| Agents API | Autenticación, permisos, validación y admisión | HTTP 202 significa admitido/encolado, no aceptado |
| TaskBus | Persistencia y coordinación de tareas | No prueba worker activo |
| Worker | Invoca CatedralX y transporta entradas/salidas | No produce las dimensiones de gobernanza ni acepta independientemente |
| Candidate Store | Persistencia e integridad del candidato | No certifica corrección semántica |
| Verificador | Verificación independiente | No sustituye autorización humana |
| Acceptance/Distribution | Registro de decisión de aceptación y entrega condicionada | No prueba recepción/procesamiento remoto |
| Producto/analytics | Fuente upstream de entradas de negocio | Productor concreto de puntuaciones aún no localizado |

## 3. Flujos distintos

### Evaluación económica canónica

`POST /v1/request` con objetivo `catedralx_economic_evaluation` → validación → TaskBus → worker → snapshot CatedralX → `DecisionResult` → candidato → verificación independiente → aceptación → distribución condicionada.

No confundir con `catedralx_opportunity_evaluation`, `catedralx_economic_proposal_review` ni el planner/LLM general de `core.master.Master`.

### Contrato de entrada de gobernanza

`central_dimensions_bp` contiene cinco puntuaciones enteras [0, 10000]:
- `viability_bp`
- `human_value_bp`
- `distribution_bp`
- `resilience_bp`
- `evidence_bp`

`dynamic_constraints` contiene métricas dinámicas como `reinvestment_surplus`, `entropy_rate`, `entropy_time_scale`, `loop_gain`, `signal_count`, `optimal_signal_count`, `exposure`, `max_exposure`, `resilience` y `minimum_resilience`.

**No mezclar los objetos:** `reinvestment_surplus` pertenece a `dynamic_constraints`, no a `central_dimensions_bp`. CatedralX consume `viability_bp) como entrada explícita; no se debe afirmar que lo deriva automáticamente de la etiqueta de decisión.

Agents valida forma, claves, tipos y rangos y transporta ambos objetos sin generarlos. El evaluador dinámico existe en CatedralX, pero no se ha demostrado que un productor upstream conectado suministre datos empresariales reales.

## 4. Procedencia

La atestación criptográfica de la observación de mercado no acredita por sí misma el origen de las cinco puntuaciones de gobernanza. La validación estructural tampoco prueba fuente, método, periodo/grano, frescura ni calidad semántica.

El contrato inspeccionado no impone un sobre de procedencia por dimensión. Debe diseñarse y versionarse con propietario, fuente, grano, fórmula/versión, timestamp, frescura, unidad/escala, referencia de evidencia y digest verificable.

## 5. Trazabilidad, autoridad y aceptación

Distinguir:
- `trace_id` / `traceparent`: correlación de ejecución.
- `evaluation_hash`: identidad/integridad del resultado de evaluación.
- `candidate_id`: candidato operacional.
- `decision_id`: identificador usado por el contrato de aceptación de Agents.
- `execution_id`: ejecución/despacho, si ocurre.

No usar `acceptance_id` como clave canónica del endpoint de aceptación: el contrato inspeccionado utiliza `decision_id`.

HMAC-SHA256 de Candidate Store y Ed25519 de aceptación tienen propósitos distintos. La validez criptográfica prueba integridad/autenticidad respecto de una clave, no corrección semántica ni conveniencia económica.

Una evaluación `ACCEPTED` de CatedralX no equivale a aceptación independiente de Agents. Autorización humana, aceptación, despacho y ejecución son etapas distintas.

## 6. Matriz estática y brechas

| Elemento | Estado observado | Límite |
|---|---|---|
| CatedralX, API, TaskBus, worker y stores | Código localizado | No prueba runtime |
| Contrato `DecisionResult` | Código localizado | No prueba todos los consumidores |
| `central_dimensions_bp` | Validación/transporte/consumo y fixtures | Productor real no localizado |
| `dynamic_constraints` | Evaluador en CatedralX; transporte en Agents | Productor upstream real no demostrado |
| Procedencia de puntuaciones | No exigida por contrato estructural actual | Requiere contrato versionado |
| E2E con entradas reales | No demostrado | Fixtures sintéticos no bastan |
| Integración de producción | No inspeccionada | Requiere evidencia operacional |

### Brechas prioritarias

1. Identificar el sistema y responsable que produce las cinco puntuaciones.
2. Identificar el productor de métricas dinámicas y sus fuentes.
3. Definir procedencia y frescura verificables por dimensión.
4. Ejecutar E2E reproducible API → TaskBus → worker → CatedralX → `DecisionResult`.
5. Probar por separado aceptación con `decision_id`, firma, autorización y distribución.

## 7. Evidencia y madurez

- **Existe:** archivo o contrato localizado.
- **Invocado:** llamada estática identificada.
- **Protegido:** test localizado.
- **Integrado:** prueba entre componentes ejecutada y registrada.
- **Operativo:** runtime observado con entorno, commit, fecha y trazas.
- **Aceptado:** verificación independiente y registro aplicable.

Esta revisión fue estática. No ejecutó código, tests, CI, contenedores ni servicios; no inspeccionó secretos, bases de datos operativas o telemetría.

## 8. Fuentes

- [CatedralX README](https://github.com/Chalamandra-Magistral-deco/CatedralX/blob/main/README.md)
- [CatedralX multicriteria.py](https://github.com/Chalamandra-Magistral-deco/CatedralX/blob/main/elite/REASON/multicriteria.py)
- [CatedralX percepcion.py](https://github.com/Chalamandra-Magistral-deco/CatedralX/blob/main/elite/PERCEIVE/percepcion.py)
- [Agents task_contracts.py](https://github.com/Chalamandra-Magistral-deco/chalamandra-agent-engine/blob/main/api/task_contracts.py)
- [Agents worker_loop.py](https://github.com/Chalamandra-Magistral-deco/chalamandra-agent-engine/blob/main/api/worker_loop.py)
- [Agents acceptance_store.py](https://github.com/Chalamandra-Magistral-deco/chalamandra-agent-engine/blob/main/api/acceptance_store.py)
