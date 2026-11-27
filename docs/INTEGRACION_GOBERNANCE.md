# Integración de Gobernanza

> **Estado reconciliado — 2026-11-23.** El contrato, la validación estructural y el transporte de `dynamic_constraints` y `central_dimensions_bp` están implementados en Agents. El productor de producto/analytics que debe generar esas dimensiones con datos reales y procedencia verificable sigue pendiente; también falta una prueba E2E vigente con esas entradas reales.
>
> **Distinciones obligatorias:** contrato implementado ≠ datos disponibles ≠ evaluación favorable ≠ aceptación independiente ≠ operación ejecutada.
>
> Este flujo (`catedralx_economic_evaluation`) es distinto de `catedralx_opportunity_evaluation`, que evalúa oportunidades de negocio mediante el CLI `BIN/catedral economic-opportunity`, y de `catedralx_economic_proposal_review`, reservado al ingreso autenticado desde CatedralX. No intercambiar sus payloads ni sus garantías.
>
> Las referencias a tareas/fixtures de octubre de 2026 que aparecen en el historial de este documento son evidencia histórica acotada a ese snapshot; no describen el estado actual de producción.

## 1. Qué es

`central_dimensions_bp` es el objeto JSON que el productor de producto/analytics debe enviar en el payload de `POST /v1/request` para que CatedralX pueda evaluar la gobernanza con entradas explícitas.

Agents valida el contrato y transporta los valores recibidos; **no calcula, inventa ni completa** estas dimensiones.

Sin entradas verificables, CatedralX debe fallar cerrado con el estado correspondiente (`UNCERTAIN`, `BLOCKED_MISSING_INPUT` u otro estado definido por el contrato). No se debe fabricar una evaluación favorable mediante defaults.

## 2. Las dimensiones

- `human_value_bp`: entero en [0, 10000], peso 20 por ciento.
- `distribution_bp`: entero en [0, 10000], peso 20 por ciento.
- `resilience_bp`: entero en [0, 10000], peso 25 por ciento.
- `evidence_bp`: entero en [0, 10000], gate mayor o igual a 8500.
- `reinvestment_surplus`: Decimal mayor a 0, gate mayor a 0.

`viability_bp` lo deriva CatedralX del estado de decisión:
- `buy` = 10000.
- `sell` = 0.
- `hold` = 5000.

Cada dimensión debe incluir procedencia suficiente en el sistema productor: fuente, periodo/observación, método de cálculo, timestamp/frescura y referencia de evidencia. El esquema concreto de metadatos debe seguir el contrato versionado del productor; Agents no debe improvisarlo.

## 3. Fórmula del score

`score_bp = (3500 * viability_bp + 2500 * resilience_bp + 2000 * human_value_bp + 2000 * distribution_bp) / 10000`

Umbrales:
- `score_bp >= 7000` produce `ACCEPTED`, sujeto a que todos los gates y entradas requeridas se satisfagan.
- `score_bp < 7000` produce `REJECTED`.

El score no reemplaza gates de evidencia, déficit, incertidumbre ni validación de procedencia.

## 4. Estados finales

- `ACCEPTED` con `missing_inputs` vacío produce `ACCEPTED`.
- `REJECTED` con `missing_inputs` vacío produce `REJECTED`.
- `BLOCKED_EVIDENCE` produce `UNCERTAIN`.
- `BLOCKED_DEFICIT` produce `UNCERTAIN`.
- `BLOCKED_UNCERTAINTY` con `missing_inputs` no vacío produce `BLOCKED_MISSING_INPUT`.
- `BLOCKED_UNCERTAINTY` con `missing_inputs` vacío produce `UNCERTAIN`.

Estos son estados de evaluación de CatedralX. No equivalen al estado del candidato en Agents, a autorización humana ni a aceptación independiente.

## 5. Ejemplo de payload válido

El payload de ejemplo debe incluir:
- objetivo explícito correspondiente al flujo;
- observaciones/precios requeridos por el contrato;
- `dynamic_constraints` con estructura válida;
- `central_dimensions_bp` con las cinco dimensiones dentro de rango;
- procedencia y frescura exigidas por el contrato de producto/analytics.

No utilizar un ejemplo sintético como evidencia de disponibilidad de datos reales ni como prueba de integración productiva.

## 6. Ejemplo bloqueado

Una solicitud con objetivo `financial_analysis` y solo precios, sin `central_dimensions_bp` ni datos equivalentes de gobernanza, debe bloquearse o producir incertidumbre según el contrato. No debe sustituirse la información ausente por ceros, constantes por perfil ni valores por defecto.

Resultado esperado: estado de bloqueo/ incertidumbre con `missing_inputs` y razones de bloqueo explícitas, incluida `DYNAMIC_GOVERNANCE_NOT_VERIFIED` cuando corresponda a la ruta.

## 7. Estado de implementación y evidencia

| Elemento | Estado documental |
|---|---|
| Validación estructural y límites de contrato en `api/task_contracts.py` | Implementado en código |
| Transporte de `dynamic_constraints` y `central_dimensions_bp` por `api/worker_loop.py` | Implementado en código |
| Invocación del adaptador canónico de CatedralX y conservación del resultado | Implementado en código |
| Productor producto/analytics con datos reales y procedencia | Pendiente |
| Prueba E2E vigente con entradas reales del productor | Pendiente |
| Estado operativo actual de producción | No demostrado por esta documentación |

Los registros de tareas/fixtures descritos en snapshots anteriores (incluidos los identificadores históricos de octubre de 2026) no deben presentarse como estado actual. Para afirmar estado de producción se requiere evidencia nueva, acotada por entorno, fecha, commit, configuración no secreta, identificadores de traza y resultado observable.

## 8. Pendientes de integración

1. Definir el productor propietario de las cinco dimensiones.
2. Documentar su contrato versionado, origen, frescura y procedencia.
3. Enviar `central_dimensions_bp` y `dynamic_constraints` en `POST /v1/request`.
4. Ejecutar pruebas de contrato positivas y negativas.
5. Ejecutar E2E con entradas reales o un dataset controlado cuya procedencia esté documentada.
6. Verificar que la salida de CatedralX se conserva sin reinterpretación.
7. Verificar por separado la aceptación independiente y la distribución condicionada.

## 9. No hacer

- No calcular `central_dimensions_bp` en Agents.
- No usar valores fijos por perfil como fallback.
- No bajar `EVIDENCE_MIN_BP` para pasar el filtro.
- No rellenar con cero los campos ausentes.
- No tratar `ACCEPTED` de CatedralX como aceptación independiente de Agents.
- No tratar aceptación como autorización humana ni como ejecución.
- No declarar producción operativa basándose únicamente en tests, fixtures o archivos presentes.

## 10. Referencia de arquitectura

La matriz completa de responsabilidades, flujos, controles criptográficos, estados de madurez y brechas está en [ARQUITECTURA_CANONICA_CATEDRALX_AGENTS.md](./ARQUITECTURA_CANONICA_CATEDRALX_AGENTS.md).
