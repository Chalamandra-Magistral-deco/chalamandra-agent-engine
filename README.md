# Chalamandra Agent Engine

Motor Python de agentes y herramientas de Chalamandra Magistral. Es una biblioteca de ejecución —no un servicio persistente— cuyo punto de entrada es `core.master.Master`.

## Arquitectura

- `core/`: descomposición de tareas, planificación, perfiles de agentes, enrutamiento, validación y ejecución.
- `adapters/`: contrato común de solicitudes/respuestas y adaptadores de Ollama, Gemini, OpenAI, Anthropic y DeepSeek.
- `registry/`: inventario de modelos y separación explícita entre capacidades declaradas y capacidades verificadas.
- `pipeline/`: síntesis, ejecución paralela, análisis dinámico y minería local de términos de búsqueda.
- `dynamics/`: simulación de dos estados con retardo, análisis de trayectorias, estabilidad y barridos.
- `telemetry/`: trazas y estadísticas de respuesta.
- `security/`: cliente de Azure Key Vault.
- `tests/`: pruebas unitarias e integración simulada.

## Agentes especializados

`core/agent_registry.py` define cinco perfiles deterministas: `coordinator` (CATEDRAL), `engineer`, `auditor`, `sentinel` y `analyst`. El enrutamiento clasifica subtareas usando su descripción y skill; `Master.inspect()` enumera los perfiles y `Master.route_agent()` permite inspeccionar una asignación antes de ejecutar.

`Executor` incluye el perfil asignado y sus herramientas autorizadas en el resultado y los metadatos de la solicitud. La lista de herramientas se filtra contra las herramientas registradas. `Master.execute_agent_tool_call(agent, {"name": ..., "arguments": {...}})` valida la forma de la llamada, que la herramienta esté registrada, que el perfil tenga permiso y que los argumentos encajen con la firma del handler antes de ejecutarlo. `Master.execute_agent_tool(agent, tool, ...)` se conserva como adaptador compatible a esa interfaz. `Master.execute_tool(...)` es una interfaz de confianza para el código anfitrión, no debe exponerse directamente a modelos ni a contenido no confiable.

Ejemplo de invocación anfitriona (el agente debe tener permiso para la herramienta y los argumentos deben satisfacer su contrato):

```python
result = await engine.execute_agent_tool_call(
    "analyst",
    {
        "name": "catedralx_reasoning",
        "arguments": {"prices": [1.08, 1.081, 1.079]},
    },
)
```

Esta frontera de ejecución ya está disponible para llamadas estructuradas originadas por el host. No implica que los adaptadores de modelo devuelvan llamadas nativas a herramientas ni que `Executor` las ejecute automáticamente; esa integración queda pendiente y debe reutilizar esta validación en lugar de invocar handlers directamente.

Los perfiles son controles declarativos iniciales, no cinco procesos autónomos aislados. El modelo recibe instrucciones de rol, pero no obtiene ejecución directa del sistema operativo. Los cambios de archivos, despliegues, operaciones financieras y cambios en campañas requieren una interfaz anfitriona explícita y sus propias autorizaciones. `sentinel` no dispone todavía de un escáner Solidity/EVM registrado: no afirmar detección automatizada de vulnerabilidades hasta integrar y probar herramientas específicas.

## Requisitos y puesta en marcha

Se requiere Python 3.12 o superior. Para preparar un entorno limpio:

```bash
python3 -m venv venv
. venv/bin/activate
python -m pip install -e ".[dev]"
cp config/.env.example config/.env
```

Edita `config/.env` localmente. El archivo real está excluido por `.gitignore`; no guardes claves en este repositorio. El ejemplo contiene únicamente nombres de variables y valores no secretos.

Ollama es el único adaptador verificado por la suite local actual. Para habilitarlo, debe responder en `OLLAMA_BASE_URL` y debe existir el modelo `OLLAMA_MODEL` (por defecto `qwen2.5:3b`). Las APIs de nube solo se activan cuando está configurada la clave correspondiente. La presencia de un adaptador o una clave no equivale a una capacidad verificada.

## Configuración

- `GEMINI_API_KEY`, `GEMINI_MODEL`: activación y selección de Gemini.
- `OPENAI_API_KEY`, `OPENAI_MODEL`: activación y selección de OpenAI.
- `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`: activación y selección de Anthropic.
- `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`: activación y selección de DeepSeek.
- `OLLAMA_BASE_URL`, `OLLAMA_MODEL`: endpoint y modelo local.
- `DEFAULT_TIMEOUT_SECONDS`, `DEFAULT_MAX_TOKENS`: valores iniciales de cada `ModelRequest`; los valores explícitos de una solicitud prevalecen.
- `CHALAMANDRA_SEARCH_TERMS_DIR`: raíz permitida para CSV de minería. La herramienta es de solo lectura y solo propone candidatos; no modifica campañas.
- `AZURE_KEYVAULT_URI`: necesaria si se llama al helper de Key Vault directamente.

## Verificación

```bash
python -m pytest -q
python -m ruff check adapters core dynamics pipeline registry security telemetry tests
python -m mypy adapters core dynamics pipeline registry security telemetry
python -m compileall -q core dynamics adapters pipeline registry security telemetry tests
python -m pip check
python -m pip wheel . --no-deps -w /tmp/chalamandra-build
```

Prueba de humo real contra el Ollama local (requiere el servicio y el modelo descargado):

```bash
python tests/test_ollama.py
```

Inspección del motor sin invocar modelos:

```python
from core.master import Master

engine = Master()
print(engine.inspect())
print(engine.orchestrator.adapter_states())
```

Las pruebas predeterminadas no llaman a proveedores de nube ni generan gasto por inferencia. Los adaptadores de nube requieren pruebas de integración separadas y una autorización expresa para usar sus credenciales.

## Límites operativos

- `Master` se consume desde Python; este repositorio no define por ahora un servidor HTTP, CLI ni servicio de sistema.
- Una habilidad solo se marca como verificada cuando existe evidencia local de prueba; las capacidades declaradas por un proveedor no elevan por sí solas el estado.
- La herramienta `search_term_miner` nunca escribe en Google Ads. Solo lee el CSV contenido en el directorio permitido y devuelve recomendaciones candidatas.
- Los límites de paso y los valores no finitos de la simulación se validan antes de calcular para reducir el riesgo de asignaciones excesivas o trayectorias inválidas.
