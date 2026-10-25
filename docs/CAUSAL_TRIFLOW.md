# Chalamandra Magistral — prototipo de causalidad tridireccional

## Objetivo

Este prototipo comprueba el primer circuito vertical sin reemplazar el motor
CatedralX, sin editar la rama main y sin desplegar nada en Azure.

El modelo separa tres planos:

- **Control:** entrada, permisos, asignación y cierre de un trabajo.
- **Evidencia:** entrada con hash, salida del motor, trazas de agentes y
  contraevidencia.
- **Entrega:** el estado de validación se genera mediante reglas deterministas;
  no se toma del resultado de un agente.

## Circuito incluido

1. POST /v1/jobs recibe objetivo, activo y al menos 30 precios explícitos.
2. El adaptador invoca pipeline/catedralx_tool.py; no reimplementa CatedralX.
3. El proveedor de revisión devuelve un contrato AgentReview.
4. governance/verification.py evalúa criterios independientes.
5. DecisionResult contiene trace_id, job_id, evidencia, supuestos,
   contraevidencia, estado de validación y procedencia.

La primera iteración es síncrona y usa memoria del proceso. No es todavía un
sistema persistente de jobs, una cola, un scheduler ni una configuración de
producción.

## Regla de evidencia

El texto de un modelo se conserva como salida generativa y no se convierte
automáticamente en evidencia. Para que una revisión pueda contribuir a la
aceptación, el adaptador de confianza debe producir EvidenceRecord con
producer=external_tool, fuente y hash de contenido. Un agente no dispone de un
campo para declarar el resultado accepted.

Si falta evidencia, la revisión falla o quedan contradicciones, el estado no
será accepted. Los precios suministrados por el solicitante quedan marcados
como caller_payload y no se presentan como datos de mercado autenticados.

## Limitaciones deliberadas

- La autenticación de producción no está conectada. La dependencia HTTP por
  defecto devuelve 503 y falla cerrada; los tests inyectan una identidad falsa.
- La idempotencia y los trabajos viven en memoria y no funcionan entre procesos.
- El proveedor de agentes está desactivado por defecto para no invocar modelos
  o generar costes inesperados.
- Activar CHALAMANDRA_ENABLE_AGENT_REVIEW permite una revisión preliminar por
  Master, pero esa salida generativa sigue siendo parcial y no puede aceptar
  el resultado sin evidencia estructurada de herramientas.
- No hay broker, scheduler, entrega webhook, reintentos durables ni verificación
  de callbacks.
- No se ha comprobado el estado actual de la VM ni se ha hecho una prueba contra
  un checkout local del motor CatedralX.

## Prueba local

Requiere Python 3.12 o superior y una instalación de desarrollo:

    python -m pip install -e ".[dev,api]"
    python -m pytest -q

Para habilitar la membrana local, configurar primero un adaptador real de
identidad como dependencia; sin él, las rutas de trabajo deben responder 503.
Configurar CATEDRALX_PATH para apuntar al checkout real de CatedralX y mantener
CATEDRALX_STATE_PATH fuera del árbol de código.

Antes de producción aún se requieren almacenamiento duradero, autenticación JWT
real, autorización por propietario y scope, proveedor de evidencia de confianza,
revisión de seguridad, pruebas de carga y una ejecución controlada en staging.
