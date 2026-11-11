"""Master: punto de entrada del sistema de agentes."""

from __future__ import annotations

import inspect
import types
import typing
from collections.abc import Mapping, Sequence
from typing import Any, get_args, get_origin, get_type_hints

from pydantic import TypeAdapter

from adapters.base import Skill
from core.decomposer import Decomposer
from core.agent_registry import AgentRegistry
from core.executor import Executor
from core.orchestrator import Orchestrator
from core.skill_router import SkillRouter
from core.model_router import ModelRouter
from core.tool_registry import ToolRegistry
from pipeline.dynamics_tool import analyze_dynamics
from pipeline.catedralx_tool import catedralx_reasoning_tool
from pipeline.search_term_miner import mine_search_terms_tool
from pipeline.lead_classifier import classify_lead


class Master:
    """
    Punto de control confiable para planificar, enrutar y ejecutar tareas.

    La instancia registra las herramientas integradas, conecta los routers con el
    orquestador y expone un límite de ejecución mediado por el host. La existencia
    de una herramienta no equivale a autorización: las llamadas estructuradas deben
    pasar por `execute_agent_tool_call` cuando procedan de un agente.

    Example:
        master = Master()
        inventory = master.inspect()
        assert "tools" in inventory and "agents" in inventory.
    """

    def __init__(self) -> None:
        """
        Inicializa los componentes centrales y registra las herramientas integradas.

        Side effects:
            Construye el orquestador y consulta la configuración de credenciales.
            Si `AZURE_KEYVAULT_URI` está configurado y falta una clave de proveedor,
            la inicialización puede consultar Azure Key Vault. No ejecuta herramientas.

        Example:
            master = Master()
            print(master.tools.available())
        """
        self.orchestrator = Orchestrator()
        self.tools = ToolRegistry()
        self.router = SkillRouter(self.orchestrator, self.tools)
        self.model_router = ModelRouter(self.orchestrator)
        self.decomposer = Decomposer(self.orchestrator)
        self.agent_registry = AgentRegistry()
        self.executor = Executor(self)

        self._register_core_tools()

    def _register_core_tools(self) -> None:
        """
        Registra las herramientas de dominio integradas en el ToolRegistry del motor.

        Args:
            None aparte de la instancia `self`.

        Returns:
            None.

        Raises:
            ValueError: Si una herramienta con el mismo nombre ya está registrada.
            ImportError: Si no se puede importar un handler integrado requerido.

        Side effects:
            Modifica el registro de herramientas de la instancia; no ejecuta ningún handler ni publica nada.

        Example:
            master = Master()  # La inicialización registra las herramientas integradas
        """

        self.tools.register(
            name="dynamics",
            description="Analiza y simula sistemas dinámicos con retardo.",
            skills=("analysis", "reasoning"),
            handler=analyze_dynamics,
        )

        self.tools.register(
            name="search_term_miner",
            description=(
                "Analiza un CSV autorizado de términos de búsqueda de Ads; "
                "genera candidatos de keywords, negativas y n-grams sin "
                "modificar campañas."
            ),
            skills=("analysis", "research", "data"),
            handler=mine_search_terms_tool,
        )

        self.tools.register(
            name="catedralx_reasoning",
            description=(
                "Ejecuta el motor financiero de CatedralX con al menos 30 precios "
                "válidos, o usa data_source='frankfurter_daily' para obtener "
                "hasta 500 observaciones de referencia EUR/USD desde Frankfurter. "
                "Devuelve clasificación buy/sell/hold, impulso, volatilidad, drawdown "
                "y diagnósticos complementarios. signal_strength es una heurística, "
                "no una probabilidad. Solo análisis: trading permanece deshabilitado "
                "y no se crean ni ejecutan órdenes."
            ),
            skills=("analysis", "reasoning"),
            handler=catedralx_reasoning_tool,
        )

        self.tools.register(
            name="lead_classifier",
            description=(
                "Clasifica un lead del formulario de contacto: tipo, urgencia "
                "y propuesta sugerida. Determinista, sin LLM, sin red. "
                "Guarda en JSONL local. No envía respuesta al cliente."
            ),
            skills=("analysis", "research", "data"),
            handler=classify_lead,
        )

    def inspect(self) -> dict:
        """
        Devuelve un inventario operativo sin exponer valores de credenciales.

        Returns:
            dict: Claves `models`, `tools`, `agents`, `capabilities` y
            `credential_status`. El estado de credenciales incluye fuentes/estados, no
            secretos.

        Raises:
            Propaga errores inesperados de los componentes de registro u orquestación.

        Side effects:
            No ejecuta herramientas ni tareas; consulta estado en memoria.

        Example:
            inventory = Master().inspect()
            names = inventory["tools"]
        """
        return {
            "models": self.orchestrator.available_models(),
            "tools": self.tools.available(),
            "agents": self.agent_registry.available(self.tools.available()),
            "capabilities": self.orchestrator.capabilities(),
            # Only source/status metadata is exposed, never credential values.
            "credential_status": dict(getattr(self.orchestrator, "credential_status", {})),
        }

    def route_skill(self, skill: str | Skill) -> dict[str, list[str]]:
        """
        Resuelve los modelos y las herramientas asociados a una skill.

        Args:
            skill (str | Skill): Skill que se va a enrutar, como `"analysis"`.

        Returns:
            dict[str, list[str]]: Modelos verificados, fallbacks, modelos activos y
            herramientas compatibles, según el contrato de `SkillRouter.route`.

        Raises:
            Propaga errores inesperados del router o del registro.

        Side effects:
            Ninguno intencional; no ejecuta los modelos ni las herramientas.

        Example:
            route = Master().route_skill("analysis")
            print(route["tools"])
        """
        return self.router.route(skill)

    def route_agent(self, description: str, skill: str = "reasoning") -> dict:
        """
        Selecciona un perfil de agente y muestra solo sus herramientas registradas.

        Args:
            description (str): Descripción de la tarea que guía la selección determinista.
            skill (str): Skill principal; por defecto `"reasoning"`.

        Returns:
            dict: Perfil serializado con identidad, misión, skills, restricciones y
            `allowed_tools` intersectadas con el registro actual.

        Raises:
            Propaga `ValueError` si el registro no puede resolver el perfil.

        Security:
            La salida describe permisos; no ejecuta ninguna herramienta ni concede
            permisos fuera de la allowlist declarada por el perfil.

        Example:
            profile = Master().route_agent("Analiza datos de mercado", "analysis")
            print(profile["name"], profile["allowed_tools"])
        """
        profile = self.agent_registry.route(description, skill)
        return profile.to_dict(self.tools.available())

    def route_models(
        self,
        skill: str | Skill,
        *,
        allow_fallback: bool = True,
    ) -> list[str]:
        """
        Obtiene los modelos compatibles con una skill, incluyendo fallbacks opcionales.

        Args:
            skill (str | Skill): Skill requerida.
            allow_fallback (bool): Si es `True`, agrega modelos activos cuya skill esté
                reclamada pero no verificada. Si es `False`, solo devuelve modelos
                activos con evidencia local de la skill.

        Returns:
            list[str]: Nombres de modelos en el orden definido por `ModelRouter`.

        Raises:
            Propaga errores inesperados del router.

        Side effects:
            No invoca proveedores; consulta el registro de capacidades.

        Example:
            verified = Master().route_models("analysis", allow_fallback=False)
        """
        return self.model_router.route(
            skill,
            allow_fallback=allow_fallback,
        )

    def select_model(
        self,
        skill: str | Skill,
        *,
        allow_fallback: bool = True,
    ) -> str | None:
        """
        Selecciona el primer modelo de la ruta de una skill.

        Args:
            skill (str | Skill): Skill que debe atenderse.
            allow_fallback (bool): Permite usar modelos activos no verificados cuando no
                haya una alternativa verificada o después de estas.

        Returns:
            str | None: Nombre del primer modelo elegible, o `None` si no existe ninguno.

        Raises:
            Propaga errores inesperados del router.

        Side effects:
            No llama al proveedor ni ejecuta una tarea.

        Example:
            model_name = Master().select_model("reasoning")
        """
        return self.model_router.select(
            skill,
            allow_fallback=allow_fallback,
        )

    async def plan(self, task: str) -> dict:
        """
        Descompone una tarea y prepara rutas, permisos y modelos por subtarea.

        Args:
            task (str): Instrucción original que debe planificarse.

        Returns:
            dict: Incluye la tarea, `subtask_count` y `steps`; cada paso contiene la
            subtarea, perfil de agente, modelos verificados/activos/fallback y las
            herramientas permitidas tras intersectar skill y perfil.

        Raises:
            Propaga errores no recuperables del descomponedor u orquestador.

        Side effects:
            Ejecuta la fase de planificación del modelo configurado (puede consumir
            recursos locales o una API, según el adapter activo). No ejecuta las
            herramientas planeadas ni publica resultados.

        Example:
            plan = await Master().plan("Analiza la estabilidad del sistema")
        """
        subtasks = await self.decomposer.decompose(task)

        steps = []
        for st in subtasks:
            route = self.router.route(st.skill)
            models = self.model_router.route(st.skill)
            profile = self.agent_registry.route(st.description, st.skill)
            agent_data = profile.to_dict(self.tools.available())
            allowed_tools = set(agent_data["allowed_tools"])
            routed_tools = [name for name in route["tools"] if name in allowed_tools]

            steps.append(
                {
                    "subtask": st.to_dict(),
                    "agent": agent_data,
                    "models": models,
                    "verified_models": self.model_router.verified(st.skill),
                    "fallback_models": self.model_router.fallback(st.skill),
                    "active_models": self.model_router.active(st.skill),
                    "tools": routed_tools,
                }
            )

        return {
            "task": task,
            "subtask_count": len(subtasks),
            "steps": steps,
        }

    @staticmethod
    def _json_schema_type(annotation: Any) -> dict[str, Any]:
        """
        Mapea una anotación Python a un tipo básico de JSON Schema.

        Args:
            annotation (Any): Anotación de tipo que se inspeccionará, incluidos tipos opcionales o contenedores reconocidos.

        Returns:
            dict[str, Any]: Fragmento JSON Schema que representa el tipo inferido; para tipos desconocidos usa el esquema genérico definido por el método.

        Raises:
            No genera excepciones de dominio; puede propagar errores de introspección en anotaciones inusuales.

        Side effects:
            Ninguno; no ejecuta el handler ni valida un payload real.

        Example:
            schema = Master._json_schema_type(str)
        """

        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin in (list, tuple, set, Sequence):
            item = args[0] if args else str
            return {"type": "array", "items": Master._json_schema_type(item)}
        if origin in (types.UnionType, typing.Union):
            non_none = [arg for arg in args if arg is not type(None)]
            return (
                Master._json_schema_type(non_none[0]) if len(non_none) == 1 else {"type": "string"}
            )
        if annotation is str:
            return {"type": "string"}
        if annotation is bool:
            return {"type": "boolean"}
        if annotation is int:
            return {"type": "integer"}
        if annotation is float:
            return {"type": "number"}
        if annotation is dict or origin is dict:
            return {"type": "object", "additionalProperties": True}
        return {"type": "string"}

    def tool_schemas(self, names: list[str]) -> list[dict[str, Any]]:
        """
        Construye esquemas JSON para llamadas a las herramientas solicitadas.

        Args:
            names (list[str]): Nombres de herramientas ya registradas y autorizadas por
                el contexto que solicita los esquemas.

        Returns:
            list[dict[str, Any]]: Esquemas de función con nombre, descripción, tipos
            inferidos de anotaciones y parámetros requeridos.

        Raises:
            ValueError: Si alguno de los nombres no está registrado.
            TypeError | ValueError: Si no se puede inspeccionar la firma de un handler.

        Security:
            Generar un esquema no ejecuta ni autoriza por sí solo la herramienta. El
            límite de confianza debe validar perfil, allowlist y argumentos al ejecutar.

        Example:
            schemas = Master().tool_schemas(["dynamics"])
        """
        schemas = []
        for name in names:
            spec = self.tools.get(name)
            signature = inspect.signature(spec.handler)
            try:
                hints = get_type_hints(spec.handler)
            except (NameError, TypeError):
                hints = {}
            properties: dict[str, Any] = {}
            required: list[str] = []
            for parameter_name, parameter in signature.parameters.items():
                if parameter.kind in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                ):
                    continue
                annotation = hints.get(parameter_name, parameter.annotation)
                properties[parameter_name] = self._json_schema_type(annotation)
                if parameter.default is inspect.Parameter.empty:
                    required.append(parameter_name)
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                }
            )
        return schemas

    async def execute_tool(self, tool: str, **kwargs):
        """
        Invoca una herramienta registrada desde el contexto confiable del host.

        Args:
            tool (str): Nombre exacto de la herramienta registrada.
            **kwargs (Any): Argumentos que debe aceptar la firma del handler.

        Returns:
            Any: Valor retornado por el handler, esperando handlers async y resultados
            awaitable de handlers síncronos.

        Raises:
            ValueError: Si el nombre no está registrado.
            Exception: Propaga las excepciones del handler.

        Security:
            Esta interfaz no aplica permisos por perfil; úsala solo desde código confiable.
            Para llamadas propuestas por un agente, usa `execute_agent_tool_call`.

        Side effects:
            Dependen del handler: puede escribir estado local o consultar fuentes externas.

        Example:
            result = await Master().execute_tool(
                "dynamics", a=1.0, b=1.0, alpha=0.5, gamma=0.2, tau=0.1,
                duration=1.0, dt=0.01,
            )
        """
        return await self.tools.execute(tool, **kwargs)

    async def execute_agent_tool_call(self, agent: str, tool_call: Mapping[str, Any]) -> Any:
        """
        Valida el contrato y los permisos antes de ejecutar una llamada estructurada.

        Args:
            agent (str): Nombre de un perfil de agente registrado.
            tool_call (Mapping[str, Any]): Objeto con exactamente `name` (str) y
                `arguments` (Mapping[str, Any]); no acepta código ni texto libre ejecutable.

        Returns:
            Any: Resultado real del handler autorizado.

        Raises:
            TypeError: Si `tool_call` o `arguments` no son mappings válidos.
            ValueError: Si las claves son incorrectas, el nombre no existe o los
                argumentos no cumplen la firma/anotaciones del handler.
            PermissionError: Si el perfil no tiene permiso para la herramienta.
            Exception: Puede propagar excepciones del handler tras superar la validación.

        Security:
            Rechaza claves adicionales, comprueba el registro y la allowlist del perfil,
            enlaza la firma e intenta validar los tipos anotados con Pydantic. No
            convierte texto del modelo en código; los efectos posteriores pertenecen al
            handler. Las reglas de autorización deben seguir aplicándose en este límite.

        Example:
            result = await master.execute_agent_tool_call(
                "analyst",
                {"name": "dynamics", "arguments": {
                    "a": 1.0, "b": 1.0, "alpha": 0.5, "gamma": 0.2, "tau": 0.1,
                    "duration": 1.0, "dt": 0.01,
                }},
            )
        """
        if not isinstance(tool_call, Mapping):
            raise TypeError("tool_call debe ser un objeto con name y arguments.")
        if set(tool_call) != {"name", "arguments"}:
            raise ValueError("tool_call debe contener únicamente name y arguments.")

        tool_name = tool_call.get("name")
        arguments = tool_call.get("arguments")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_call.name debe ser un nombre no vacío.")
        if not isinstance(arguments, Mapping):
            raise TypeError("tool_call.arguments debe ser un objeto.")

        # Confirmar que existe antes de comprobar autorización evita ejecutar
        # nombres arbitrarios y produce un error controlado para herramientas ausentes.
        spec = self.tools.get(tool_name)
        profile = self.agent_registry.get(agent)
        registered_allowed = set(profile.tools_available(self.tools.available()))
        if tool_name not in registered_allowed:
            raise PermissionError(
                f"El agente {profile.name!r} no tiene permiso para usar {tool_name!r}."
            )

        try:
            signature = inspect.signature(spec.handler)
            signature.bind(**dict(arguments))
            try:
                hints = get_type_hints(spec.handler)
            except (NameError, TypeError):
                hints = {}
            for argument_name, argument_value in arguments.items():
                parameter = signature.parameters[argument_name]
                annotation = hints.get(argument_name, parameter.annotation)
                if annotation is not inspect.Parameter.empty:
                    TypeAdapter(annotation).validate_python(argument_value)
        except Exception as exc:
            if (
                isinstance(exc, (TypeError, ValueError, KeyError))
                or type(exc).__name__ == "ValidationError"
            ):
                raise ValueError(
                    f"Argumentos inválidos para la herramienta {tool_name!r}: {exc}"
                ) from exc
            raise

        return await self.execute_tool(tool_name, **dict(arguments))

    async def execute_agent_tool(self, agent: str, tool: str, **kwargs):
        """
        Adaptador de compatibilidad para la interfaz de ejecución anterior.

        Args:
            agent (str): Perfil de agente que debe autorizar la herramienta.
            tool (str): Nombre de la herramienta registrada.
            **kwargs (Any): Argumentos de la herramienta.

        Returns:
            Any: Resultado devuelto por `execute_agent_tool_call`.

        Raises:
            TypeError | ValueError | PermissionError: Según los errores de contrato,
            existencia o autorización detectados por el límite estructurado.
            Exception: Propaga errores del handler.

        Security:
            Conserva el mismo control de permisos del contrato estructurado; no es una
            vía alternativa para saltarse la allowlist.

        Example:
            result = await master.execute_agent_tool(
                "analyst", "dynamics", a=1.0, b=1.0, alpha=0.5, gamma=0.2, tau=0.1,
                duration=1.0, dt=0.01,
            )
        """
        return await self.execute_agent_tool_call(
            agent,
            {"name": tool, "arguments": kwargs},
        )

    async def execute_task(
        self,
        task: str,
        *,
        allowed_tools: Sequence[str] | None = None,
        trace_id: str | None = None,
    ):
        """
        Descompone y ejecuta una tarea usando los perfiles y límites del motor.

        Args:
            task (str): Instrucción a ejecutar.
            allowed_tools (Sequence[str] | None): Allowlist opcional de nombres; cuando
                se proporciona, restringe todas las subtareas además de los permisos del
                perfil. `None` no añade una restricción global extra.

        Returns:
            ExecutionSummary: Resumen con los resultados, estado y errores por subtarea.

        Raises:
            ValueError: Si la allowlist no es una secuencia de nombres válidos o incluye
                herramientas no registradas.
            Exception: Puede propagar errores no capturados del descomponedor o ejecutor.

        Side effects:
            Puede llamar al adapter de modelo seleccionado y ejecutar herramientas
            autorizadas. Sus costes, red y persistencia dependen de los adapters y
            handlers activos. No publica ni despliega por sí mismo.

        Example:
            summary = await master.execute_task(
                "Analiza la estabilidad del sistema", allowed_tools=["dynamics"]
            )
        """
        if allowed_tools is not None:
            if isinstance(allowed_tools, (str, bytes)) or any(
                not isinstance(name, str) or not name.strip() for name in allowed_tools
            ):
                raise ValueError("allowed_tools debe ser una secuencia de nombres válidos")
            permitted = set(allowed_tools)
            unknown = permitted.difference(self.tools.available())
            if unknown:
                raise ValueError(f"Herramientas no registradas: {sorted(unknown)}")
            allowed_tools = sorted(permitted)
        if trace_id is None:
            # Preserve the pre-tracing call contract for existing adapters and test doubles.
            subtasks = await self.decomposer.decompose(task)
            if allowed_tools is None:
                return await self.executor.execute_plan(task, subtasks)
            return await self.executor.execute_plan(
                task, subtasks, allowed_tools=allowed_tools
            )

        subtasks = await self.decomposer.decompose(task, trace_id=trace_id)
        if allowed_tools is None:
            return await self.executor.execute_plan(task, subtasks, trace_id=trace_id)
        return await self.executor.execute_plan(
            task,
            subtasks,
            allowed_tools=allowed_tools,
            trace_id=trace_id,
        )
