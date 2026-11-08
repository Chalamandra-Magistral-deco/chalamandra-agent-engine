"""Reconcile independently accepted business-cycle candidates into CatedralX feedback.

This process consumes only acceptance records already verified and written by the
Agents API. It never changes model parameters, enables trading, or claims business
impact from acceptance; commercial outcomes require separately observed evidence.
"""
from __future__ import annotations

import fcntl
import importlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api import acceptance_store, candidate_store, crypto_signer


def _catedralx_root() -> Path:
    configured = os.getenv("CATEDRALX_PATH", "").strip()
    root = (
        Path(configured).expanduser()
        if configured
        else Path(__file__).resolve().parents[2] / "CatedralX"
    )
    root = root.resolve(strict=True)
    if not (root / "elite" / "FEX" / "GOVERNANCE" / "economic_cycle.py").is_file():
        raise FileNotFoundError("CATEDRALX_PATH no contiene FEX/GOVERNANCE/economic_cycle.py")
    return root


def _load_economic_cycle(root: Path):
    elite_path = str((root / "elite").resolve())
    if elite_path not in sys.path:
        sys.path.insert(0, elite_path)
    existing = sys.modules.get("FEX")
    if existing is not None:
        paths = {Path(value).resolve() for value in getattr(existing, "__path__", [])}
        expected = (root / "elite" / "FEX").resolve()
        if paths and expected not in paths:
            raise RuntimeError("Ya existe un paquete FEX de otra copia de CatedralX; feedback bloqueado")
    module = importlib.import_module("FEX.GOVERNANCE.economic_cycle")
    expected_file = (root / "elite" / "FEX" / "GOVERNANCE" / "economic_cycle.py").resolve()
    module_file = getattr(module, "__file__", None)
    if not isinstance(module_file, str):
        raise RuntimeError("El módulo economic_cycle no tiene una ruta de archivo verificable")
    if Path(module_file).resolve() != expected_file:
        raise RuntimeError("Se cargó un módulo economic_cycle ajeno a CATEDRALX_PATH")
    return module


def _read_acceptances(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"acceptance_store.jsonl inválido en línea {line_number}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"Registro de aceptación no-objeto en línea {line_number}")
            records.append(record)
    return records


def reconcile_feedback_once(
    *,
    ledger_path: Path | None = None,
    acceptance_path: Path | None = None,
) -> dict[str, Any]:
    """Append verify+feedback for business candidates with valid Ed25519 acceptance."""
    if not os.getenv("CHALAMANDRA_VERIFIER_PUBLIC_KEY", "").strip():
        raise ValueError("CHALAMANDRA_VERIFIER_PUBLIC_KEY no está configurada")
    expected_verifier_id = os.getenv("CHALAMANDRA_VERIFIER_ID", "").strip()
    if not expected_verifier_id:
        raise ValueError("CHALAMANDRA_VERIFIER_ID no está configurado")

    root = _catedralx_root()
    cycle_module = _load_economic_cycle(root)
    ledger = (ledger_path or cycle_module.ledger_path()).expanduser().resolve()
    acceptance_file = acceptance_path or acceptance_store._acceptance_file()
    lock_path = ledger.with_suffix(ledger.suffix + ".feedback.lock")
    processed: list[str] = []
    skipped = 0
    failures: list[dict[str, str]] = []

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        try:
            records = _read_acceptances(Path(acceptance_file))
            for record in records:
                candidate_id = record.get("candidate_id")
                outcome = record.get("outcome")
                if not isinstance(candidate_id, str) or outcome not in {"accepted", "rejected"}:
                    skipped += 1
                    continue
                signature = record.get("signature")
                if not isinstance(signature, str) or not signature:
                    failures.append({"candidate_id": candidate_id, "reason": "signature_missing"})
                    continue
                if record.get("verifier_id") != expected_verifier_id:
                    failures.append({"candidate_id": candidate_id, "reason": "verifier_identity_mismatch"})
                    continue
                if not crypto_signer.verify_decision(record, signature):
                    failures.append({"candidate_id": candidate_id, "reason": "signature_invalid"})
                    continue

                candidate = candidate_store.get_candidate(candidate_id)
                if not candidate:
                    failures.append({"candidate_id": candidate_id, "reason": "candidate_missing"})
                    continue
                expected_digest = candidate_store.compute_evidence_digest(candidate)
                if record.get("evidence_digest") != expected_digest:
                    failures.append({"candidate_id": candidate_id, "reason": "evidence_digest_mismatch"})
                    continue
                cycle_id = candidate.get("business_cycle_id")
                if not isinstance(cycle_id, str) or not cycle_id.strip():
                    # Financial predictions are labeled through FEX.FEEDBACK.feedback_loop
                    # when real observed market outcomes are available, not by acceptance.
                    skipped += 1
                    continue

                summary = cycle_module.resumir_ciclos(ledger)
                matching = next((row for row in summary["cycles"] if row.get("cycle_id") == cycle_id), None)
                if matching is None:
                    failures.append({"candidate_id": candidate_id, "reason": "business_cycle_missing"})
                    continue
                stage = matching.get("stage")
                if stage == "feedback":
                    skipped += 1
                    continue
                if stage not in {"authorize", "verify"}:
                    failures.append({"candidate_id": candidate_id, "reason": "business_cycle_stage_invalid"})
                    continue

                now = datetime.now(timezone.utc).isoformat()
                if stage == "authorize":
                    evidence = (
                        "Ed25519 independent acceptance verified; "
                        f"decision_id={record.get('decision_id')}; candidate_id={candidate_id}; "
                        f"evidence_digest={expected_digest}; trace_id={record.get('trace_id')}"
                    )
                    verification_result = (
                        "accepted_for_structural_review; commercial_impact_unmeasured"
                        if outcome == "accepted"
                        else "rejected_by_independent_acceptance; commercial_impact_unmeasured"
                    )
                    cycle_module.registrar_evento(
                        "verify",
                        {
                            "evidence": evidence,
                            "result": verification_result,
                            "verified_at": now,
                            "candidate_id": candidate_id,
                            "decision_id": record.get("decision_id"),
                            "trace_id": record.get("trace_id"),
                            "independent_check": "ed25519_signature_verifier_identity_and_candidate_digest",
                            "evidence_recounted": True,
                            "evidence_recount_method": "recompute_candidate_evidence_digest",
                            "evidence_recount_scope": "candidate_integrity_only_commercial_impact_unmeasured",
                        },
                        path=ledger,
                        cycle_id=cycle_id,
                    )
                    stage = "verify"

                if stage == "verify":
                    if outcome == "accepted":
                        lesson = (
                            "El candidato superó la validación independiente; esto no demuestra por sí solo "
                            "demanda, ingresos ni impacto comercial."
                        )
                        next_action = "medir_resultado_comercial_observado_antes_de_afirmar_impacto"
                    else:
                        lesson = "El candidato fue rechazado por la validación independiente; revisar evidencia y criterios."
                        next_action = "corregir_evidencia_y_solicitar_nueva_revision"
                    cycle_module.registrar_evento(
                        "feedback",
                        {"lesson": lesson, "next_action": next_action,
                         "candidate_id": candidate_id, "decision_id": record.get("decision_id"),
                         "outcome": outcome, "trace_id": record.get("trace_id")},
                        path=ledger,
                        cycle_id=cycle_id,
                    )
                processed.append(cycle_id)
        finally:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)

    return {
        "processed_cycles": processed,
        "processed_count": len(processed),
        "skipped": skipped,
        "errors": failures,
        "trading_enabled": False,
        "automatic_retraining": False,
    }
