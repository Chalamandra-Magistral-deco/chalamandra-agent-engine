# Integración de Gobernanza

Estado: contrato documentado; implementación pendiente de coordinación con producto.

## Qué es

central_dimensions_bp es el objeto JSON que producto/analytics debe enviar
en el payload de POST /v1/request para que CatedralX produzca un
DecisionResult con status ACCEPTED.

Sin este objeto, CatedralX falla cerrado con UNCERTAIN o BLOCKED_MISSING_INPUT
porque no tiene datos de governance verificables.

## Las 5 dimensiones

- human_value_bp: int en [0, 10000], peso 20 por ciento
- distribution_bp: int en [0, 10000], peso 20 por ciento
- resilience_bp: int en [0, 10000], peso 25 por ciento
- evidence_bp: int en [0, 10000], gate mayor o igual a 8500
- reinvestment_surplus: Decimal mayor a 0, gate mayor a 0

viability_bp lo deriva CatedralX del state.decision:
buy=10000, sell=0, hold=5000.

## Fórmula del score

score_bp = (3500 veces viability_bp + 2500 veces resilience_bp + 2000 veces human_value_bp + 2000 veces distribution_bp) dividido 10000

Umbrales:
- score_bp mayor o igual a 7000 produce ACCEPTED
- score_bp menor a 7000 produce REJECTED

## Estados finales

- ACCEPTED con missing_inputs vacío produce ACCEPTED
- REJECTED con missing_inputs vacío produce REJECTED
- BLOCKED_EVIDENCE produce UNCERTAIN
- BLOCKED_DEFICIT produce UNCERTAIN
- BLOCKED_UNCERTAINTY con missing_inputs no vacío produce BLOCKED_MISSING_INPUT
- BLOCKED_UNCERTAINTY con missing_inputs vacío produce UNCERTAIN

## Ejemplo de payload válido

Objeto con objetivo catedralx_economic_proposal_review, payload con
precio_historico, precio_actual, dynamic_constraints (growth_allowed true,
issues vacío) y central_dimensions_bp con los 5 valores válidos.

## Ejemplo bloqueado

Objeto con objetivo financial_analysis y payload solo con precios, sin
central_dimensions_bp.

Resultado esperado: BLOCKED_MISSING_INPUT con missing_inputs que incluye
evidence, human_value, distribution, reinvestment, resilience y
blocking_reasons con DYNAMIC_GOVERNANCE_NOT_VERIFIED.

## Estado de producción actual

Dos tasks bloqueadas en tasks.db:
- Task 1 (38993a20): UNCERTAIN por governance incompleta. Fixture sintético.
- Task 2 (6ba41ddf): stale_observation (snapshot viejo).

Ambos son comportamiento correcto del fail-closed, no bugs.

## Pendientes de integración

1. Producto/analytics calcula los 5 campos.
2. Enviar central_dimensions_bp en POST /v1/request.
3. Configurar dynamic_constraints con growth_allowed true.
4. Re-ejecutar flujos E2E contra payload con governance real.

## NO hacer

- No calcular central_dimensions_bp en Agents (rompe separación)
- No usar valores fijos por perfil (fallback estático)
- No bajar EVIDENCE_MIN_BP para pasar el filtro
- No rellenar con 0 los campos ausentes
