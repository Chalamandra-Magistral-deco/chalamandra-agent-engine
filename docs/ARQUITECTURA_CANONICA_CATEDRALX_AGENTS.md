# Arquitectura canónica CatedralX ↔ Agents

> **Estado:** referencia de arquitectura contrastada estáticamente con `main`; no certifica runtime ni producción.  
> **Fecha de inspección:** 2026-11-23.  
> **Repositorios:** [CatedralX](https://github.com/Chalamandra-Magistral-deco/CatedralX) · [Chalamandra Agent Engine](https://github.com/Chalamandra-Magistral-deco/chalamandra-agent-engine)  
> **Alcance:** documentación y correspondencia de arquitectura. No se modificó código funcional.

## 1. Propósito y límites

Chalamandra Magistral DecoX desarrolla una arquitectura en la que el razonamiento, la coordinación, la autorización, la ejecución y la aceptación son responsabilidades diferenciadas.

- **CatedralX:** motor de razonamiento y análisis financiero experimental. Produce resultados estructurados y evidencia dentro de su alcance implementado.
- **Agents:** motor coordinador Python, API HTTP y worker persistente. Orquesta solicitudes, integra modelos/herramientas y opera flujos explícitos.
- **Membrana contractual:** patrón de controles distribuido en API, contratos, TaskBus, worker, almacenes y verificador. No se documenta como microservicio independiente.
- **Verificador independiente:** comprueba los criterios y la evidencia del candidato antes de aceptación.
- **Productos/analytics:** sistemas productores de entradas de negocio; su disponibilidad y calidad deben demostrarse por separado.

No describir CatedralX como motor universal ni como sistema de trading de producción. Su README delimita el alcance actual como análisis financiero experimental; las etiquetas `buy`, `sell` y `hold` son salidas analíticas, no órdenes ni recomendaciones de inversión.

## 2. Responsabilidades y fronteras

| Componente | Responsabilidad | No debe asumirse que hace |
|---|---|---|
| CatedralX | Procesar observaciones, aplicar lógica analítica y restricciones, construir `DecisionResult`, conservar trazas/evidencia | Ejecutar órdenes o autorizar por sí mismo operaciones externas |
| Agents API | Autenticar, validar contratos/permisos, admitir solicitudes y correlacionar trazas | Fabricar datos de gobernanza o convertir una propuesta en autorización |
| TaskBus | Persistir y coordinar tareas | Probar que un worker está activo en producción |
| Agents worker | Invocar la ruta canónica de CatedralX y publicar candidatos | Aceptar de forma independiente su propio resultado |
| Candidate Store | Persistir candidatos y su autenticidad/integridad | Certificar corrección semántica |
| Verificador independiente | Comprobar criterios, evaluación y evidencia; registrar aceptación según contrato | Sustituir la autorización humana o garantizar verdad económica |
| Acceptance/Distribution stores | Registrar aceptación y condicionar entrega | Demostrar por sí solos que un consumidor remoto recibió/procesó el resultado |
| Producto/analytics | Producir entradas de gobernanza basadas en datos y procedencia | Delegar el cálculo de esas entradas a Agents |

## 3. Flujos que no deben mezclarse

### 3.1 Evaluación económica canónica

`POST /v1/request` con objetivo explícito `catedralx_economic_evaluation` → validación de API/contrato → TaskBus → worker → adaptador canónico de CatedralX → `DecisionResult` → candidato → verificación independiente → aceptación → distribución condicionada.

Esta ruta determinista no es el planner/LLM general de `core.master.Master`.

### 3.2 Otras rutas de CatedralX

El README de Agents distingue:
- `catedralx_reasoning`: herramienta de análisis de precios enrutable al perfil analyst; no equivale a evaluación económica gobernada.
- `catedralx_opportunity_evaluation`: evaluación de oportunidades mediante CLI `BIN/catedral economic-opportunity`.
- `catedralx_economic_proposal_review`: ruta reservada al ingreso autenticado desde CatedralX.

No intercambiar payloads, estados ni garantías entre estos flujos.

### 3.3 Distribución y orquestación

La distribución debe consumir un candidato aceptado y autorizado según el contrato aplicable. Una intención de trabajo iniciada internamente sigue sometida a autenticación, autorización, límites de herramienta y trazabilidad. Ninguno de estos flujos habilita ejecución financiera real por defecto.

## 4. Membrana contractual y trazabilidad

Los controles de frontera se aplican en componentes existentes:
- API y autenticación/autorización.
- `api/task_contracts.py`.
- TaskBus.
- Worker y adaptadores.
- Candidate Store.
- Verificador independiente.
- Acceptance Store y Distribution Store.

Los identificadores y campos deben documentarse según el contrato real de cada flujo. No asumir que todos los payloads contienen los mismos campos. En particular, distinguir:
- `trace_id` / `traceparent`: correlación de ejecución.
- `catedralx_trace_id`: correlación específica de la ruta CatedralX, solo donde esté presente en el contrato.
- `evaluation_hash`: identidad/integridad de una evaluación concreta.
- `candidate_id`, `acceptance_id`, `execution_id`: identidades de etapas distintas, no intercambiables.

La presencia de un identificador en un test o esquema no prueba que se propague en todos los caminos productivos.

## 5. Integridad, autorización y aceptación

- HMAC-SHA256 autentica registros de candidatos mediante una clave compartida entre API y worker; no es una firma de autoridad humana.
- Ed25519 se usa en el mecanismo de aceptación independiente según su contrato y material de claves.
- La validez criptográfica prueba autenticidad/integridad respecto de la clave, no la corrección semántica o económica.
- Una evaluación `ACCEPTED` de CatedralX no equivale a una aceptación independiente del candidato en Agents.
- La autorización humana y la aceptación independiente son controles distintos.
- La ejecución/dispatch requiere autorización válida y condiciones del flujo; no se infiere de una propuesta ni de una aceptación aislada.

## 6. Matriz de correspondencia estática

| Requisito | Evidencia localizada | Estado de lectura estática | Límite |
|---|---|---|---|
| Motor analítico CatedralX | `elite/`, `BIN/`, README | Presente | Alcance financiero experimental |
| Contrato `DecisionResult` | `elite/contracts/` y grafo de dependencias | Presente | No prueba que todo consumidor lo acepte |
| API Agents | `api/server.py` | Presente | Runtime no inspeccionado |
| Contratos de tareas | `api/task_contracts.py` | Presente | Cobertura por ruta requiere tests |
| TaskBus persistente | `api/task_bus.py` | Presente | No prueba worker activo |
| Worker y adaptador CatedralX | `api/worker_loop.py` y adaptador canónico | Presente | Ejecución real no observada en esta inspección |
| Candidate Store | `api/candidate_store.py` | Presente | No prueba completitud de registros en producción |
| Verificación independiente | `verifier/independent_acceptance_verifier.py` | Presente | Claves/configuración/runtime no inspeccionados |
| Acceptance Store | `api/acceptance_store.py` | Presente | Ledger de producción no inspeccionado |
| Distribution Store | `api/distribution_store.py` | Presente | Entrega remota no demostrada |
| Gobernanza dinámica | `dynamic_constraints`, `central_dimensions_bp` | Contrato/transporte/validación presentes según documentación de integración | Productor de datos reales y E2E vigente pendientes |
| Trazas | `trace_id`, `traceparent` en rutas documentadas | Presencia localizada | Propagación universal no demostrada |
| Integración de producción | No se inspeccionó runtime ni entorno desplegado | No demostrada | Requiere evidencia operacional acotada |

## 7. Brecha de gobernanza: estado reconciliado

En Agents, `api/task_contracts.py` valida estructura y límites; `api/worker_loop.py` transporta `dynamic_constraints` y `central_dimensions_bp` sin generarlos; el adaptador invoca CatedralX y conserva el resultado.

**Pendiente:** el productor de producto/analytics debe generar y entregar las dimensiones de gobernanza respaldadas por datos reales; también falta una prueba E2E vigente con esas entradas reales.

Por tanto:
1. Contrato implementado no significa que los datos estén disponibles.
2. Transporte implementado no significa que la evaluación pueda completarse.
3. Evaluación favorable no significa aceptación independiente.
4. Aceptación no significa que una operación externa haya sido ejecutada.

Agents no debe calcular, inventar, completar con ceros ni sustituir las dimensiones faltantes. El comportamiento fail-closed debe conservarse.

## 8. Evidencia y madurez

Usar estas categorías sin colapsarlas:
1. **Existe:** archivo, módulo o contrato localizado.
2. **Invocado:** llamada identificada en una ruta estática.
3. **Protegido:** test localizado que cubre la frontera.
4. **Integrado:** prueba de interacción entre componentes ejecutada y registrada.
5. **Operativo:** evidencia de runtime con entorno, configuración, fecha y trazas.
6. **Aceptado:** verificación independiente y registro de aceptación aplicable.

Esta revisión fue de lectura estática de archivos GitHub. No ejecutó código, tests, CI, contenedores ni servicios; no inspeccionó secretos, claves, bases de datos operativas o telemetría de producción. Los estados de CI consultados no aportaron checks verificables para los HEAD revisados.

## 9. Brechas y orden de cierre

| Prioridad | Brecha | Evidencia requerida para cerrar |
|---|---|---|
| P0 | Productor de `central_dimensions_bp` | Contrato productor, origen de datos, frescura, procedencia y validación |
| P0 | E2E con gobernanza real | Ejecución reproducible, fixture/dataset identificado, commit, entorno y resultado |
| P0 | Nomenclatura de estados | Tabla canónica que separe estado de evaluación, autorización, aceptación, dispatch y ejecución |
| P1 | Propagación completa de trazas | Tests por ruta y trace correlacionado de extremo a extremo |
| P1 | Verificación de aceptación/distribución | Pruebas positivas, negativas, replay, clave ausente/inválida y entrega condicionada |
| P1 | Estado CI y runtime | Checks de CI y evidencia de entorno desplegado; no inferir desde archivos |
| P2 | Contratos de productos | Productor/consumidor, versión de esquema, propietario y SLO por producto |

## 10. Protocolo de cambio

OBSERVAR → INTERPRETAR → RECOMENDAR → AUTORIZAR → EJECUTAR → VERIFICAR → DOCUMENTAR.

Esta documentación no autoriza cambios de código, despliegues, cambios de secretos, migraciones, ejecución de operaciones ni ampliación de permisos. Toda intervención funcional requiere autorización específica y evidencia antes/después.

## 11. Fuentes primarias

- [CatedralX README](https://github.com/Chalamandra-Magistral-deco/CatedralX/blob/main/README.md)
- [CatedralX grafo de dependencias](https://github.com/Chalamandra-Magistral-deco/CatedralX/blob/main/docs/GRAFO_DEPENDENCIAS_VERIFICADO.md)
- [Agents README](https://github.com/Chalamandra-Magistral-deco/chalamandra-agent-engine/blob/main/README.md)
- [Agents integración de gobernanza](https://github.com/Chalamandra-Magistral-deco/chalamandra-agent-engine/blob/main/docs/INTEGRACION_GOVERNANCE.md)
- [Agents worker](https://github.com/Chalamandra-Magistral-deco/chalamandra-agent-engine/blob/main/api/worker_loop.py)
- [Agents verificador](https://github.com/Chalamandra-Magistral-deco/chalamandra-agent-engine/blob/main/verifier/independent_acceptance_verifier.py)
