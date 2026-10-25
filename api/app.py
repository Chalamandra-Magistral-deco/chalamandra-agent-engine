"""API del prototipo causal tridireccional.

La autenticación real debe inyectarse antes de cualquier despliegue. La
implementación por defecto falla cerrada y no acepta tokens sin verificar.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from governance.contracts import DecisionResult, FinancialAnalysisRequest
from governance.financial_flow import ReviewProvider, request_hash, run_financial_flow


@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str]


def auth_not_configured() -> Principal:
    """Dependencia segura por defecto: ninguna identidad se presume válida."""
    raise HTTPException(
        status_code=503,
        detail=(
            "La membrana no tiene un validador JWT/identidad configurado. "
            "El acceso está cerrado por defecto."
        ),
    )


def create_app(
    *,
    analyzer: Callable[..., Any] | None = None,
    reviewer: ReviewProvider | None = None,
    principal_dependency: Callable[..., Principal] = auth_not_configured,
) -> FastAPI:
    """Construye una app de prueba con dependencias reemplazables.

    Persistencia y deduplicación son únicamente locales al proceso. No usar
    esta implementación en producción ni escalarla a varias instancias.
    """
    app = FastAPI(
        title="Chalamandra Magistral — Membrana de decisión",
        version="0.1.0-prototype",
        description=(
            "Prototipo de contrato para solicitud, análisis CatedralX y "
            "verificación independiente. Sin despliegue productivo."
        ),
    )
    app.state.jobs = {}
    app.state.idempotency = {}
    app.state.mutation_lock = asyncio.Lock()

    @app.get("/health")
    async def health() -> dict[str, str]:
        # Solo indica que el proceso responde; no acredita dependencias sanas.
        return {"status": "process_alive", "mode": "prototype"}

    @app.post("/v1/jobs", response_model=DecisionResult)
    async def create_job(
        payload: FinancialAnalysisRequest,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=16, max_length=128)
        ],
        principal: Principal = Depends(principal_dependency),
    ) -> DecisionResult:
        if "jobs:create" not in principal.scopes:
            raise HTTPException(status_code=403, detail="Falta el scope jobs:create.")

        body_hash = request_hash(payload)
        cache_key = (principal.subject, idempotency_key)

        # Prototipo serializado: evita dos ejecuciones simultáneas con la misma
        # clave en este proceso. No equivale a persistencia ni entrega exactly-once.
        async with app.state.mutation_lock:
            prior = app.state.idempotency.get(cache_key)
            if prior is not None:
                prior_hash, prior_result = prior
                if prior_hash != body_hash:
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency-Key reutilizada con un payload distinto.",
                    )
                return prior_result

            result = await run_financial_flow(
                payload,
                analyzer=analyzer if analyzer is not None else _default_analyzer,
                reviewer=reviewer,
            )
            app.state.idempotency[cache_key] = (body_hash, result)
            app.state.jobs[result.job_id] = (principal.subject, result)
            return result

    @app.get("/v1/jobs/{job_id}", response_model=DecisionResult)
    async def get_job(
        job_id: str,
        principal: Principal = Depends(principal_dependency),
    ) -> DecisionResult:
        if "jobs:read" not in principal.scopes:
            raise HTTPException(status_code=403, detail="Falta el scope jobs:read.")
        row = app.state.jobs.get(job_id)
        if row is None or row[0] != principal.subject:
            raise HTTPException(status_code=404, detail="Trabajo inexistente.")
        return row[1]

    @app.get("/v1/jobs/{job_id}/result", response_model=DecisionResult)
    async def get_job_result(
        job_id: str,
        principal: Principal = Depends(principal_dependency),
    ):
        result = await get_job(job_id, principal)
        if result.status.value != "accepted":
            return JSONResponse(
                status_code=202,
                content=result.model_dump(mode="json"),
            )
        return result

    return app


async def _default_analyzer(request: FinancialAnalysisRequest) -> dict[str, Any]:
    from governance.financial_flow import analyze_with_catedralx

    return await analyze_with_catedralx(request)


app = create_app()
