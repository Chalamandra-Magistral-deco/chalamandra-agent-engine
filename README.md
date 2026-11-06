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
        "arguments": {"data_source": "frankfurter_daily", "observations": 60},
    },
)
```

El adaptador Ollama ya envía los esquemas de herramientas autorizadas y normaliza las llamadas nativas devueltas por el modelo. `Executor` procesa el ciclo modelo → llamada propuesta → validación del host → resultado de herramienta → respuesta del modelo, con un máximo de tres rondas por subtarea. Las llamadas se ejecutan mediante `Master.execute_agent_tool_call`; los errores de validación se devuelven al modelo como resultados fallidos y nunca se convierten en ejecución arbitraria. Los demás adaptadores no reciben ejecución automática de llamadas nativas en esta fase; cualquier llamada inesperada procedente de otro adaptador se rechaza.

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
- `CHALAMANDRA_API_HOST`, `CHALAMANDRA_API_PORT`: la API solo permite loopback; por defecto `127.0.0.1:8766`.
- `CHALAMANDRA_API_TOKEN`: token bearer para los POST y para todas las rutas cuando se configura.
- `CHALAMANDRA_API_ENABLE_MODEL_CALLS`: apagado por defecto. Para activar planificación/ejecución de tareas exige además un token.
- `CHALAMANDRA_MAGIS_JWT_SECRET`: secreto independiente para la identidad server-to-server `magis-service`; no reutilizar el JWT general ni el de CatedralX.
- `CHALAMANDRA_MAGIS_EXECUTION_ENABLED`: gate exclusivo del producto MAGIS; permanece `false` por defecto y no habilita `/v1/plan` ni `/v1/tasks` generales. API y Worker deben recibir el mismo estado aprobado de este gate.


## API operativa local

Ejecuta desde la raíz del repositorio después de instalar dependencias:

```bash
python -m api.server
```

Rutas disponibles:
- `GET /healthz`: estado básico del servidor y política de ejecución.
- `GET /v1/inspect`: modelos, herramientas, perfiles y capacidades registradas; nunca devuelve valores secretos.
- `GET /v1/agents`, `GET /v1/tools`: descubrimiento de perfiles/herramientas.
- `POST /v1/tool-calls`: invoca una herramienta estructurada y valida el perfil autorizado mediante `Master.execute_agent_tool_call`.
- `POST /internal/v1/products/magis/requests`: admisión de texto MAGIS con JWT `magis-service`, scope `magis:execute`, contrato cerrado e idempotencia por operación.
- `GET /internal/v1/products/magis/jobs/{task_id}` y `GET /internal/v1/products/magis/results/{candidate_id}`: lectura limitada a tareas MAGIS; los resultados solo se entregan después de aceptación independiente. Requieren scope `magis:read`.

- `POST /v1/plan`: planificación con modelos; requiere `CHALAMANDRA_API_ENABLE_MODEL_CALLS=true` y token.
- `POST /v1/tasks`: ejecución del flujo de tarea; requiere la misma activación explícita y token.

Ejemplo de comprobación autenticada sin invocar modelos (lee el token sin imprimirlo):

```bash
python - <<'PY'
import json
from dotenv import dotenv_values
from urllib.request import Request, urlopen

token = dotenv_values("config/.env").get("CHALAMANDRA_API_TOKEN")
if not token:
    raise SystemExit("Falta CHALAMANDRA_API_TOKEN en config/.env")
for path in ("/healthz", "/v1/inspect", "/v1/agents", "/v1/tools"):
    req = Request(
        "http://127.0.0.1:8766" + path,
        headers={"Authorization": f"Bearer {token}"},
    )
    with urlopen(req, timeout=3) as response:
        print(path, response.status, json.load(response))
PY
```

Para instalar el servicio de usuario en esta VM:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/chalamandra-agents-api.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now chalamandra-agents-api.service
systemctl --user status chalamandra-agents-api.service
```

Los POST siempre requieren `Authorization: Bearer $CHALAMANDRA_API_TOKEN`. La API limita el tamaño del cuerpo a 64 KiB, no acepta campos extra en los contratos y no expone shell ni CORS. Sin token, el modo local solo ofrece GET de lectura. Las llamadas a modelos están desactivadas por defecto; el acceso remoto mediante HTTP directo está bloqueado. Para integrar otro sistema, usa un túnel privado autenticado o proxy TLS tras una revisión de permisos.

## Verificación

```bash
python -m pytest -q
python -m ruff check api adapters core dynamics pipeline registry security telemetry tests
python -m mypy api adapters core dynamics pipeline registry security telemetry
python -m compileall -q api core dynamics adapters pipeline registry security telemetry tests
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

- `Master` se consume desde Python y tiene una API HTTP local de loopback; la unidad `systemd --user` está versionada en `deploy/systemd/`. La persistencia de ese servicio tras cerrar sesión depende de la configuración de linger del usuario en la VM.
- Una habilidad solo se marca como verificada cuando existe evidencia local de prueba; las capacidades declaradas por un proveedor no elevan por sí solas el estado.
- La herramienta `search_term_miner` nunca escribe en Google Ads. Solo lee el CSV contenido en el directorio permitido y devuelve recomendaciones candidatas.
- Los límites de paso y los valores no finitos de la simulación se validan antes de calcular para reducir el riesgo de asignaciones excesivas o trayectorias inválidas.
