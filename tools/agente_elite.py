import json
import logging
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("EliteAgentCore")

OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    "http://127.0.0.1:11434/api/chat",
)
MODEL = "qwen2.5:3b"
TIMEOUT_SECONDS = 180
MAX_TURNS = 4

REPOS_ROOT = Path(
    os.getenv("CHALAMANDRA_REPOS_ROOT")
    or os.getenv("CHALAMANDRA_REPOS_DIR")
    or Path(__file__).resolve().parents[2]
).expanduser().resolve()


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListReposArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=10, ge=1, le=10)


def disk_usage_root(_: EmptyArgs) -> dict:
    usage = shutil.disk_usage("/")

    return {
        "path": "/",
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_percent": round(
            usage.used / usage.total * 100, 2
        ) if usage.total else None,
    }


def list_repos(args: ListReposArgs) -> dict:
    entries = sorted(
        REPOS_ROOT.iterdir(),
        key=lambda item: item.name.lower(),
    )[:args.limit]

    return {
        "path": str(REPOS_ROOT),
        "count": len(entries),
        "entries": [
            {
                "name": item.name,
                "type": (
                    "directory" if item.is_dir()
                    else "file" if item.is_file()
                    else "other"
                ),
            }
            for item in entries
        ],
    }


TOOL_MODELS = {
    "disk_usage_root": EmptyArgs,
    "list_repos": ListReposArgs,
}

TOOL_FUNCTIONS = {
    "disk_usage_root": disk_usage_root,
    "list_repos": list_repos,
}

TOOLS_MANIFEST = [
    {
        "type": "function",
        "function": {
            "name": "disk_usage_root",
            "description": "Consulta el espacio disponible en la raíz del sistema. Solo lectura.",
            "parameters": EmptyArgs.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_repos",
            "description": "Lista hasta diez elementos del directorio de repositorios configurado. No acepta otras rutas.",
            "parameters": ListReposArgs.model_json_schema(),
        },
    },
]


class EliteAgentCore:
    def __init__(self):
        self.history: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "Eres un asistente técnico de análisis. "
                    "Para responder al objetivo debes consultar las dos "
                    "herramientas disponibles. Son exclusivamente de lectura. "
                    "No inventes resultados ni afirmes haber ejecutado "
                    "comandos del sistema."
                ),
            }
        ]

    def dispatch(self) -> dict:
        payload = {
            "model": MODEL,
            "messages": self.history,
            "tools": TOOLS_MANIFEST,
            "stream": False,
            "options": {
                "temperature": 0,
                "num_ctx": 4096,
            },
        }

        req = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(
                req, timeout=TIMEOUT_SECONDS
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(
                f"Ollama no respondió dentro del límite: {exc}"
            ) from exc

    def execute_tool(self, call: dict) -> str:
        function = call.get("function", {})
        name = function.get("name")
        arguments = function.get("arguments", {})

        if name not in TOOL_MODELS:
            return json.dumps(
                {"error": "Herramienta no autorizada"},
                ensure_ascii=False,
            )

        if isinstance(arguments, str):
            arguments = json.loads(arguments)

        validated = TOOL_MODELS[name].model_validate(arguments)  # type: ignore[attr-defined]
        result = TOOL_FUNCTIONS[name](validated)  # type: ignore[operator]

        logger.info("Herramienta de lectura ejecutada: %s", name)
        return json.dumps(result, ensure_ascii=False)

    def run(self, prompt: str) -> str:
        logger.info("Objetivo: %s", prompt)
        self.history.append({"role": "user", "content": prompt})

        for turn in range(1, MAX_TURNS + 1):
            logger.info("Inferencia %s/%s", turn, MAX_TURNS)
            response = self.dispatch()
            message = response.get("message", {})
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                return message.get("content") or "Sin respuesta final."

            self.history.append(message)

            for call in tool_calls:
                name = call.get("function", {}).get("name", "")
                try:
                    result = self.execute_tool(call)
                except Exception as exc:
                    logger.exception("Herramienta rechazada o fallida")
                    result = json.dumps({
                        "error": str(exc),
                    }, ensure_ascii=False)

                self.history.append({
                    "role": "tool",
                    "tool_name": name,
                    "content": result,
                })

        return "Límite de inferencias alcanzado sin respuesta final."


if __name__ == "__main__":
    agent = EliteAgentCore()
    result = agent.run(
        "Consulta el espacio disponible en la raíz y lista "
        "los primeros diez elementos del directorio de repositorios configurado. "
        "Resume únicamente los resultados observados."
    )
    print("\n=== RESULTADO ELITE ===")
    print(result)
