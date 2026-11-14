# Chalamandra Agent Engine

Motor coordinador Python de Chalamandra Magistral, compuesto por el núcleo `core.master.Master`, una API HTTP local (`api.server`) y un worker persistente (`api.worker_loop`). La biblioteca puede usarse directamente desde Python; la API y el worker son procesos independientes que comparten los contratos y el TaskBus.

## Arquitectura

- `core/`: descomposición de tareas, planificación, perfiles de agentes, enrutamiento, validación y ejecución.
- `adapters/`: contrato común de solicitudes/respuestas y adaptadores de Ollama, Gemini, OpenAI, Anthropic y DeepSeek.
- `registry/`: inventario de modelos y separación explícita entre capacidades declaradas y capacidades verificadas.
- `pipeline/`: síntesis, ejecución paralela, análisis dinámico y minería local de términos de búsqueda.
- `dynamics/`: simulación de dos estados con retardo, análisis de trayectorias, estabilidad y barridos.
- `telemetry/`: trazas y estadísticas de respuesta.
- `security/`: cliente de Azure Key Vault.
- `tools/`: utilidades auxiliares de inspección; `agente_elite.py` es de solo lectura y no forma parte de la imagen de producción.
- `tests/`: pruebas unitarias e integración simulada.

## Coordinación central y conexión con CatedralX

**Agents coordina el sistema.** `Master` centraliza planificación, descomposición, selección de perfiles, enrutamiento de modelos y ejecución mediada por el host. `Master.inspect()` expone el contrato de coordinación y `Master.plan()` lo incluye junto con el agente, modelo y herramientas permitidas de cada subtarea.

CatedralX conserva el razonamiento estratégico y predictivo. Hay dos rutas existentes y distintas:

1. **Análisis mediante herramienta:** una subtarea especializada se enruta al perfil `analyst`; su allowlist puede incluir `catedralx_reasoning`. El handler es de análisis de precios y no equivale a la evaluación económica gobernada.
2. **Evaluación económica canónica:** `POST /v1/request` valida el objetivo explícito `catedralx_economic_evaluation`, permisos, procedencia y contrato; Agents la coloca en TaskBus y el worker ejecuta el adaptador canónico de CatedralX. Este recorrido determinista no invoca el planner/LLM de `Master`: evita que un modelo reinterprete o fabrique entradas del contrato económico. El worker preserva `DecisionResult`, `trace_id` y evidencia, y publica solo un candidato sujeto a aceptación independiente.

Membrana no se presupone como proceso separado: sus controles se aplican en las fronteras existentes de API, contratos, TaskBus, worker, stores y verificador. La autorización humana y la aceptación independiente siguen siendo requisitos distintos.

## Agentes especializados

`Agents` es el coordinador central del sistema: recibe objetivos, descompone y enruta tareas, asigna perfiles y herramientas autorizadas, gestiona dependencias y consolida resultados. Dentro de este motor, `core/agent_registry.py` define perfiles de ejecución, incluido `coordinator` (perfil interno de coordinación de tareas), además de `engineer`, `auditor`, `sentinel` y `analyst`. El perfil `coordinator` no convierte a CatedralX en coordinador del ecosistema. `Master.inspect()` enumera perfiles y `Master.route_agent()` permite inspeccionar asignaciones antes de ejecutar.

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
- `OLLAMA_CLOUD_ENABLED`: opt-in explícito para registrar Ollama Cloud; por defecto `false`.
- `OLLAMA_API_KEY`, `OLLAMA_CLOUD_MODEL`: credencial y modelo de Ollama Cloud (por defecto `deepseek-v4.1-flash`).
- `OLLAMA_CLOUD_INPUT_USD_PER_MILLION`, `OLLAMA_CLOUD_OUTPUT_USD_PER_MILLION`: precios configurables para estimar el coste; los defaults son $0.30/$1.20 USD por millón de tokens de entrada/salida y no descuentan tokens en caché.
- `DEFAULT_TIMEOUT_SECONDS`, `DEFAULT_MAX_TOKENS`: valores iniciales de cada `ModelRequest`; los valores explícitos de una solicitud prevalecen.
- `CHALAMANDRA_SEARCH_TERMS_DIR`: raíz permitida para CSV de minería. La herramienta es de solo lectura y solo propone candidatos; no modifica campañas.
- `AZURE_KEYVAULT_URI`: necesaria si se llama al helper de Key Vault directamente.
- `CHALAMANDRA_API_HOST`, `CHALAMANDRA_API_PORT`: la API solo permite loopback; por defecto `127.0.0.1:8766`.
- Autenticación JWT HS256: todas las rutas salvo `GET /healthz` exigen `Authorization: Bearer <JWT>` con el `scope` de la operación. `CHALAMANDRA_JWT_SECRET` es el secreto general; `CHALAMANDRA_CATEDRALX_JWT_SECRET` es el secreto de la identidad `catedralx-service` y `CHALAMANDRA_MAGIS_JWT_SECRET` el de `magis-service`. La variable antigua `CHALAMANDRA_API_TOKEN` ya no se usa: ningún endpoint la lee.
- `CHALAMANDRA_API_ENABLE_MODEL_CALLS`: apagado por defecto. Activa planificación/ejecución de tareas con llamadas a modelos.
- `CHALAMANDRA_STATE_DIR`: raíz del estado compartido API↔worker (por defecto `~/.local/state/chalamandra`); TaskBus, candidatos, aceptaciones y distribuciones viven en `<raíz>/agents`.
- `CHALAMANDRA_CANDIDATE_HMAC_KEY`: **obligatoria**. Debe contener exactamente 64 caracteres hexadecimales (32 bytes aleatorios) y tener el mismo valor en `config/.env` (API) y `config/worker.env` (worker). API y worker rechazan arrancar si falta o no es válida. Cada registro de candidato se autentica con HMAC-SHA256 sobre el registro completo; los registros sin digest, incluidos los históricos, se rechazan para aceptación y entrega. No reutilices secretos JWT ni la semilla Ed25519 del verificador. La rotación invalida los registros existentes: no la realices sin un procedimiento de migración y validación que preserve el ledger.
- `CHALAMANDRA_MAGIS_JWT_SECRET`: secreto independiente para la identidad server-to-server `magis-service`; no reutilizar el JWT general ni el de CatedralX.
- `CHALAMANDRA_MAGIS_EXECUTION_ENABLED`: gate exclusivo del producto MAGIS; permanece `false` por defecto y no habilita `/v1/plan` ni `/v1/tasks` generales. API y Worker deben recibir el mismo estado aprobado de este gate.


### Ollama Cloud (opcional)

El adaptador `ollama_cloud` está separado de `OllamaAdapter`, que sigue siendo local.
Para registrarlo se requieren ambos valores: `OLLAMA_CLOUD_ENABLED=true` y
`OLLAMA_API_KEY`. La sola presencia de la clave no activa el proveedor ni altera
el enrutamiento. El modelo predeterminado de la API es `deepseek-v4.1-flash`
(la etiqueta de CLI es `deepseek-v4.1-flash:cloud`).

El adaptador usa `https://ollama.com/api/chat`, no reintenta solicitudes facturables
y devuelve las llamadas a herramientas como propuestas sujetas a la validación del host.
El coste en `ModelResponse.cost_usd` es una estimación calculada con los contadores
de tokens reportados y los precios configurados; no se aplica descuento de caché.
Sus skills permanecen como declaradas/no verificadas hasta contar con benchmarks propios.
Las pruebas unitarias simulan HTTP y no consumen inferencia cloud.

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
import time
import jwt
from dotenv import dotenv_values
from urllib.request import Request, urlopen

secret = dotenv_values("config/.env").get("CHALAMANDRA_JWT_SECRET")
if not secret:
    raise SystemExit("Falta CHALAMANDRA_JWT_SECRET en config/.env")
token = jwt.encode(
    {"sub": "smoke-reader", "scope": "results:read", "exp": int(time.time()) + 300},
    secret,
    algorithm="HS256",
)
for path in ("/healthz", "/v1/inspect", "/v1/agents", "/v1/tools"):
    req = Request(
        "http://127.0.0.1:8766" + path,
        headers={"Authorization": f"Bearer {token}"},
    )
    with urlopen(req, timeout=3) as response:
        print(path, response.status, json.load(response))
PY
```

Para instalar el servicio de usuario en un host Linux administrado por el operador (opcional):

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/chalamandra-agents-api.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now chalamandra-agents-api.service
systemctl --user status chalamandra-agents-api.service
```

Todas las rutas salvo `GET /healthz` requieren `Authorization: Bearer <JWT HS256>` con el `scope` de la operación. La API limita el tamaño del cuerpo a 64 KiB, no acepta campos extra en los contratos y no expone shell ni CORS. Sin JWT válido, el modo local solo ofrece `GET /healthz`. Las llamadas a modelos están desactivadas por defecto; el acceso remoto mediante HTTP directo está bloqueado. Para integrar otro sistema, usa un túnel privado autenticado o proxy TLS tras una revisión de permisos.

### Docker Compose portable

El despliegue Compose no depende de rutas absolutas de una VM ni de volúmenes externos precreados. Compose administra dos volúmenes nombrados persistentes: `agents_agents_runtime_state` y `agents_catedralx_runtime_state`. El allowlist de webhooks privados no tiene IPs ni hosts de ejemplo activados por defecto.

Prepara los archivos locales de configuración (no se versionan):

```bash
cp config/.env.example config/.env
cp config/.env.example config/worker.env
```

Configura las claves públicas de verificación en ambos entornos y los secretos de API únicamente donde corresponda. Antes de iniciar Compose, selecciona imágenes que ya hayan sido construidas y verificadas:

```bash
export CATEDRALX_BASE_IMAGE=catedralx:<tag-validado>
export AGENTS_API_IMAGE=agents-api:<tag-validado>
export AGENTS_WORKER_IMAGE=agents-worker:<tag-validado>
docker compose config --quiet
docker compose up -d
```

Las operaciones FEX se ejecutan de forma puntual en el perfil `lifecycle`; no dependen de systemd, cron ni de rutas de una VM. El servicio normal API/Worker no las lanza automáticamente:

```bash
# Solo despacha ciclos que ya tengan measure → evaluate → propose → authorize.
docker compose --profile lifecycle run --rm cycle-dispatch

# Después de que el verificador independiente registre la aceptación/rechazo,
# verifica la firma y el digest y devuelve verify → feedback al ledger FEX.
docker compose --profile lifecycle run --rm feedback-reconcile
```

`cycle-dispatch` registra `run_id`, `task_id` y `trace_id`; el estado del run se consulta mediante `GET /internal/v1/orchestration/runs/{run_id}` con scope `orchestration:read`. El reconciliador de feedback no firma decisiones ni acepta candidatos: consume únicamente registros ya firmados y persistidos por Agents.

No uses etiquetas históricas como prueba de procedencia: registra los SHA de Agents y CatedralX de las imágenes elegidas y verifica sus etiquetas OCI antes de habilitar el servicio.

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

- `Master` se consume desde Python y tiene una API HTTP local de loopback; la unidad `systemd --user` está versionada en `deploy/systemd/`. La persistencia de ese servicio tras cerrar sesión depende de la configuración de linger del usuario en el host Linux.
- Una habilidad solo se marca como verificada cuando existe evidencia local de prueba; las capacidades declaradas por un proveedor no elevan por sí solas el estado.
- La herramienta `search_term_miner` nunca escribe en Google Ads. Solo lee el CSV contenido en el directorio permitido y devuelve recomendaciones candidatas.
- Los límites de paso y los valores no finitos de la simulación se validan antes de calcular para reducir el riesgo de asignaciones excesivas o trayectorias inválidas.


### Evaluación económica de oportunidades

El objetivo `catedralx_opportunity_evaluation` es independiente de `catedralx_economic_evaluation` (mercado). No exige precios históricos. Requiere ingresos, costos, capital requerido/disponible, horizonte, moneda, modelo de ingreso (`active`, `passive`, `hybrid`, `investment` o `asset`), supuestos, evidencia referenciada y escenarios explícitos conservador/base/optimista. Agents envía la tarea al contrato determinista de CatedralX y conserva el resultado en `economic_assessment`, separado de `DecisionResult`. La salida es solo analítica: no autoriza, no ejecuta órdenes y requiere aceptación independiente.
