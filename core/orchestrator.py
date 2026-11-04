"""Núcleo de ejecución de modelos del sistema Chalamandra."""

from __future__ import annotations

import asyncio
import importlib.util
import os

from adapters.base import ModelRequest, ModelResponse, Skill
from adapters.ollama import OllamaAdapter
from adapters.ollama_cloud import OllamaCloudAdapter
from adapters.gemini import GeminiAdapter
from adapters.deepseek import DeepSeekAdapter
from adapters.gpt import GPTAdapter
from adapters.claude import ClaudeAdapter
from dotenv import load_dotenv
from telemetry.trace import Tracer
from registry.models import MODELS


load_dotenv("config/.env")


def _load_optional_secret(name: str, status: dict[str, str]) -> str | None:
    """Load a provider key from the environment or Azure Key Vault.

    Key Vault is attempted only when AZURE_KEYVAULT_URI is configured, so
    local/offline development can continue with Ollama and no Azure setup.
    Failure to resolve a cloud key must not prevent the local adapter from
    starting. Only the source/status is recorded; secret values are never
    included in diagnostics.
    """
    value = os.getenv(name)
    if value:
        status[name] = "environment"
        return value

    if not os.getenv("AZURE_KEYVAULT_URI"):
        status[name] = "not_configured"
        return None

    try:
        from security.keyvault import get_secret

        # Azure Key Vault secret names allow letters, digits and hyphens,
        # not environment-style underscores (for example gemini-api-key).
        vault_name = name.lower().replace("_", "-")
        value = get_secret(vault_name)
    except Exception as exc:
        status[name] = f"unavailable:{type(exc).__name__}"
        return None

    if not value:
        status[name] = "not_found"
        return None

    status[name] = "azure_key_vault"
    return value


class Orchestrator:
    """
    Coordina adapters, capacidades y trazas de ejecución de modelos.

    Ollama se registra inicialmente como adapter local; los adapters de nube solo
    se añaden si existe la credencial correspondiente. Las skills reclamadas por el
    registro no se consideran verificadas sin evidencia local.

    Security:
        `credential_status` contiene únicamente el origen/estado de credenciales,
        nunca los valores secretos.

    Side effects:
        Puede leer configuración de entorno y, si se configura Azure Key Vault,
        recuperar secretos al inicializar; al ejecutar modelos guarda trazas.

    Example:
        orchestrator = Orchestrator()
        print(orchestrator.available_models())
    """

    def __init__(self, tracer: Tracer | None = None) -> None:
        """
        Inicializa adapters activos, estado de credenciales y el tracer de respuestas.

        Args:
            tracer (Tracer | None): Almacén de telemetría inyectado; si es `None`, se construye
                el tracer por defecto para registrar respuestas del modelo.

        Returns:
            None.

        Raises:
            ValueError: Puede propagarse si la configuración de un secreto/adaptador contiene
            un valor no válido; los proveedores sin credencial se omiten.
            OSError: Puede propagarse si falla la configuración/creación del almacén de trazas.

        Side effects:
            Registra el adapter local Ollama y agrega adaptadores remotos solo cuando las
            credenciales están disponibles. Puede consultar Azure Key Vault si su URI está
            configurada y una clave requerida no está en el entorno. No ejecuta modelos durante
            la inicialización; `credential_status` registra metadatos, nunca valores secretos.

        Example:
            orchestrator = Orchestrator()
        """

        self.credential_status: dict[str, str] = {}
        self.adapters: dict[str, object] = {
            "ollama": OllamaAdapter(),
        }

        # La nube permanece apagada salvo activación explícita. La mera presencia
        # de OLLAMA_API_KEY nunca altera el enrutamiento ni inicia llamadas remotas.
        cloud_enabled = os.getenv("OLLAMA_CLOUD_ENABLED", "false").strip().lower() in {
            "1", "true", "yes", "on"
        }
        if cloud_enabled:
            ollama_cloud_key = _load_optional_secret(
                "OLLAMA_API_KEY", self.credential_status
            )
            if ollama_cloud_key:
                self.adapters["ollama_cloud"] = OllamaCloudAdapter(
                    api_key=ollama_cloud_key
                )
        else:
            self.credential_status["OLLAMA_API_KEY"] = "disabled"

        gemini_key = _load_optional_secret(
            "GEMINI_API_KEY", self.credential_status
        )
        if gemini_key:
            self.adapters["gemini"] = GeminiAdapter(api_key=gemini_key)

        deepseek_key = _load_optional_secret(
            "DEEPSEEK_API_KEY", self.credential_status
        )
        if deepseek_key:
            self.adapters["deepseek"] = DeepSeekAdapter(api_key=deepseek_key)

        openai_key = _load_optional_secret(
            "OPENAI_API_KEY", self.credential_status
        )
        if openai_key:
            self.adapters["gpt"] = GPTAdapter(api_key=openai_key)

        anthropic_key = _load_optional_secret(
            "ANTHROPIC_API_KEY", self.credential_status
        )
        if anthropic_key:
            self.adapters["claude"] = ClaudeAdapter(api_key=anthropic_key)

        self.tracer = tracer or Tracer()

    def available_models(self) -> list[str]:
        """
        Devuelve los nombres de adapters actualmente activos.

        Returns:
            list[str]: Nombres en orden alfabético. No incluye modelos solo declarados
            en `MODELS` que no se hayan registrado en `self.adapters`.

        Raises:
            No genera excepciones deliberadas.

        Side effects:
            Ninguno; no llama a los proveedores.

        Example:
            active = orchestrator.available_models()
        """
        return sorted(self.adapters.keys())

    def register(self, name: str, adapter: object) -> None:
        """
        Registra o sustituye un adapter en el mapa activo.

        Args:
            name (str): Alias que se utilizará para invocar el adapter.
            adapter (object): Objeto que debe implementar `generate(ModelRequest)` y
                devolver un `ModelResponse` (normalmente mediante una coroutine).

        Returns:
            None.

        Raises:
            No genera excepciones deliberadas; la implementación del adapter se valida
            implícitamente al intentar usarla con `run`.

        Side effects:
            Inserta o reemplaza una entrada en memoria; no verifica la skill ni la
            disponibilidad real del proveedor.

        Example:
            orchestrator.register("custom", adapter)
        """
        self.adapters[name] = adapter

    def adapter_states(self) -> dict[str, dict[str, object]]:
        """Clasifica el estado operativo de todos los adapters conocidos.

        Estados:
          VERIFIED     -> activo y con skills verificadas.
          ACTIVE       -> activo pero sin skills verificadas.
          REGISTERED   -> adapter implementado pero no activo.
          UNAVAILABLE  -> sin adapter implementado.
        """
        known = sorted(set(MODELS) | set(self.adapters))
        result: dict[str, dict[str, object]] = {}

        for name in known:
            module_available = (
                importlib.util.find_spec(f"adapters.{name}") is not None
            )

            active = name in self.adapters
            adapter = self.adapters.get(name)
            raw_skills = getattr(adapter, "verified_skills", ()) if adapter else ()

            verified_skills = [
                skill.value if isinstance(skill, Skill) else str(skill)
                for skill in raw_skills
            ]

            if active and verified_skills:
                state = "VERIFIED"
            elif active:
                state = "ACTIVE"
            elif module_available:
                state = "REGISTERED"
            else:
                state = "UNAVAILABLE"

            result[name] = {
                "state": state,
                "registered": module_available or active,
                "active": active,
                "verified": bool(verified_skills),
                "skills_verified": sorted(verified_skills),
            }

        return result

    def capabilities(self) -> dict[str, dict[str, object]]:
        """Expone las capacidades verificadas de los adaptadores registrados."""
        result: dict[str, dict[str, object]] = {}

        for name, adapter in self.adapters.items():
            raw_skills = getattr(adapter, "verified_skills", ())

            skills_verified = [
                skill.value if isinstance(skill, Skill) else str(skill)
                for skill in raw_skills
            ]

            result[name] = {
                "registered": True,
                "skills_verified": sorted(skills_verified),
            }

        return result

    def models_for_skill(
        self,
        skill: str | Skill,
        *,
        only_verified: bool = True,
    ) -> list[str]:
        """Devuelve adapters activos compatibles con una skill.

        only_verified=True:
            solo adapters activos con evidencia local de la skill.

        only_verified=False:
            adapters activos cuya skill esté verificada o reclamada
            en el registro de modelos.
        """
        skill_name = (
            skill.value if isinstance(skill, Skill) else skill
        )

        capabilities = self.capabilities()

        if only_verified:
            verified_models: list[str] = []
            for name, info in capabilities.items():
                verified = info.get("skills_verified")
                if isinstance(verified, list) and skill_name in verified:
                    verified_models.append(name)
            return sorted(verified_models)

        active = set(self.adapters.keys())

        compatible: set[str] = set()

        for name in active:
            model_info = MODELS.get(name)

            if model_info is None:
                # Adapter registrado dinámicamente: solo contamos
                # las skills verificadas que declara directamente.
                verified = capabilities.get(name, {}).get(
                    "skills_verified",
                    [],
                )

                if isinstance(verified, list) and skill_name in verified:
                    compatible.add(name)

                continue

            if (
                skill_name in model_info.skills_verified
                or skill_name in model_info.skills_claimed
            ):
                compatible.add(name)

        return sorted(compatible)

    async def run(self, model: str, request: ModelRequest) -> ModelResponse:
        """
        Ejecuta una solicitud con un adapter activo y registra la respuesta.

        Args:
            model (str): Alias de un adapter activo.
            request (ModelRequest): Prompt, rol, parámetros y metadatos de la solicitud.

        Returns:
            ModelResponse: Respuesta retornada por el adapter.

        Raises:
            ValueError: Si el alias no está conectado al orquestador.
            Exception: Propaga errores no absorbidos por el adapter.

        Side effects:
            Invoca el adapter, lo que puede usar red/coste externo según proveedor, y
            registra la respuesta a través del `Tracer` configurado.

        Example:
            request = ModelRequest(prompt="Responde solo: OK", role=ModelRole.PLANNER)
            response = await orchestrator.run("ollama", request)
        """
        adapter = self.adapters.get(model)
        if adapter is None:
            raise ValueError(f"Modelo no conectado al orquestador: {model}")
        response = await adapter.generate(request)  # type: ignore[attr-defined]
        self.tracer.record(response)
        return response

    async def run_parallel(
        self,
        models: list[str],
        request: ModelRequest,
    ) -> dict[str, ModelResponse]:
        """
        Ejecuta una solicitud contra cada nombre de modelo sin mutar el registro.

        Args:
            models (list[str]): Lista de alias activos; nombres repetidos se ejecutan
                como instancias independientes.
            request (ModelRequest): Solicitud compartida para cada ejecución.

        Returns:
            dict[str, ModelResponse]: Resultados identificados como `modelo#índice`;
            los fallos de ejecución se representan como respuestas con errores.

        Raises:
            Propaga `asyncio.CancelledError` u otros errores que impidan completar el
            control de concurrencia antes de construir un resultado.

        Side effects:
            Ejecuta proveedores en paralelo y registra las respuestas, incluidas las
            fallidas, en el tracer.

        Example:
            results = await orchestrator.run_parallel(["ollama"], request)
        """
        tasks: list[tuple[str, str, asyncio.Task[ModelResponse]]] = []

        for index, model in enumerate(models):
            result_key = f"{model}#{index}"
            task = asyncio.create_task(self.run(model, request))
            tasks.append((result_key, model, task))

        results: dict[str, ModelResponse] = {}

        for result_key, model, task in tasks:
            try:
                results[result_key] = await task
            except Exception as e:
                results[result_key] = ModelResponse(
                    call_id=request.call_id,
                    model=model,
                    role=request.role,
                    output="",
                    errors=[f"orchestrator error: {e!r}"],
                )
                self.tracer.record(results[result_key])

        return results
