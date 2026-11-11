"""Explicit contracts for the financial task types routed by Agents.

Market observations never imply permission to run CatedralX's economic
/governance evaluation. Only the explicit economic-evaluation type may do so.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime
from typing import Any

try:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey
except ImportError:  # pragma: no cover - deployment must install PyNaCl
    BadSignatureError = Exception
    VerifyKey = None

FINANCIAL_MARKET_ANALYSIS = "financial_market_analysis"
CATEDRALX_ECONOMIC_EVALUATION = "catedralx_economic_evaluation"
CATEDRALX_ECONOMIC_PROPOSAL_REVIEW = "catedralx_economic_proposal_review"

_MARKET_REQUIRED = {"historical_prices", "current_price", "observed_at", "source"}
_DYNAMIC_REQUIRED = {
    "reinvestment_surplus", "entropy_rate", "entropy_time_scale", "loop_gain",
    "signal_count", "optimal_signal_count", "exposure", "max_exposure",
    "resilience", "minimum_resilience",
}
_CENTRAL_ALLOWED = {
    "viability_bp", "human_value_bp", "distribution_bp",
    "resilience_bp", "evidence_bp",
}
_NUMERIC_DYNAMIC = _DYNAMIC_REQUIRED - {"signal_count", "optimal_signal_count"}


def _finite_positive(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(number) and number > 0


def market_source_attestation_payload(payload: dict[str, Any]) -> bytes:
    """Canonical bytes signed by the upstream market-data provider."""
    canonical = {
        "source": payload.get("source"),
        "observed_at": payload.get("observed_at"),
        "current_price": payload.get("current_price"),
        "historical_prices": payload.get("historical_prices"),
    }
    return json.dumps(
        canonical, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _verify_market_source_attestation(payload: dict[str, Any]) -> str | None:
    public_hex = os.getenv("CHALAMANDRA_MARKET_SOURCE_PUBLIC_KEY", "").strip()
    expected_key_id = os.getenv("CHALAMANDRA_MARKET_SOURCE_KEY_ID", "").strip()
    if not public_hex or not expected_key_id:
        return "market_source_verification_not_configured"
    if VerifyKey is None:
        return "market_source_verification_unavailable"
    if not re.fullmatch(r"[0-9a-fA-F]{64}", public_hex):
        return "market_source_public_key_invalid"
    attestation = payload.get("source_attestation")
    if not isinstance(attestation, dict):
        return "market_source_attestation_required"
    if attestation.get("key_id") != expected_key_id:
        return "market_source_attestation_key_id_mismatch"
    signature_hex = attestation.get("signature")
    if not isinstance(signature_hex, str) or not re.fullmatch(r"[0-9a-fA-F]{128}", signature_hex):
        return "market_source_attestation_signature_invalid"
    try:
        VerifyKey(bytes.fromhex(public_hex)).verify(
            market_source_attestation_payload(payload), bytes.fromhex(signature_hex)
        )
    except (BadSignatureError, ValueError, TypeError, OverflowError):
        return "market_source_attestation_signature_invalid"
    return None


def _market_payload_error(payload: dict[str, Any]) -> str | None:
    missing = sorted(_MARKET_REQUIRED - set(payload))
    if missing:
        return "market_payload_missing_fields:" + ",".join(missing)
    prices = payload.get("historical_prices")
    if not isinstance(prices, list) or len(prices) < 30:
        return "historical_prices_requires_at_least_30_values"
    if any(not _finite_positive(value) for value in prices):
        return "historical_prices_must_be_finite_positive_numbers"
    if not _finite_positive(payload.get("current_price")):
        return "current_price_must_be_finite_positive_number"
    source = payload.get("source")
    if not isinstance(source, str) or not source.strip() or source.strip().lower() in {
        "worker_request", "unspecified", "unknown",
    }:
        return "market_source_must_be_explicit_and_trusted_label"
    observed_at = payload.get("observed_at")
    if not isinstance(observed_at, str) or not observed_at.strip():
        return "observed_at_must_be_timezone_aware_iso8601"
    try:
        parsed = datetime.fromisoformat(observed_at.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return "observed_at_must_be_timezone_aware_iso8601"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return "observed_at_must_be_timezone_aware_iso8601"
    return _verify_market_source_attestation(payload)


def validate_task_contract(objective: str, payload: Any) -> str | None:
    """Return a stable blocking reason, or None when the task contract is valid.

    Unknown non-financial task objectives remain governed by the generic task
    contract. The ambiguous historical alias is deliberately rejected.
    """
    if not isinstance(objective, str) or not objective.strip():
        return "task_type_required"
    task_type = objective.strip()
    if not isinstance(payload, dict):
        return "task_payload_must_be_object"
    if task_type == "financial_analysis":
        return "ambiguous_legacy_task_type_use_explicit_financial_task_type"

    if task_type == FINANCIAL_MARKET_ANALYSIS:
        return _market_payload_error(payload)

    if task_type == CATEDRALX_ECONOMIC_EVALUATION:
        market_error = _market_payload_error(payload)
        if market_error:
            return market_error
        # Governance completeness belongs to CatedralX. Forward absent or
        # incomplete dimensions so the canonical engine can emit its own
        # BLOCKED_MISSING_INPUT DecisionResult with trace and reason codes.
        dynamic = payload.get("dynamic_constraints")
        if dynamic is not None and not isinstance(dynamic, dict):
            return "dynamic_constraints_must_be_object"
        if isinstance(dynamic, dict):
            invalid_dynamic = []
            for key in _NUMERIC_DYNAMIC.intersection(dynamic):
                value = dynamic[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    invalid_dynamic.append(key)
                    continue
                try:
                    if not math.isfinite(float(value)):
                        invalid_dynamic.append(key)
                except (TypeError, ValueError, OverflowError):
                    invalid_dynamic.append(key)
            for key in {"signal_count", "optimal_signal_count"}.intersection(dynamic):
                if type(dynamic[key]) is not int or dynamic[key] < 0:
                    invalid_dynamic.append(key)
            if invalid_dynamic:
                return "dynamic_constraints_invalid_fields:" + ",".join(sorted(invalid_dynamic))

        central = payload.get("central_dimensions_bp")
        if central is not None and not isinstance(central, dict):
            return "central_dimensions_bp_must_be_object"
        if isinstance(central, dict):
            noncanonical = sorted(set(central) - _CENTRAL_ALLOWED)
            if noncanonical:
                return "central_dimensions_bp_noncanonical_fields:" + ",".join(noncanonical)
            invalid_central = [
                key for key, value in central.items()
                if type(value) is not int or not 0 <= value <= 10000
            ]
            if invalid_central:
                return "central_dimensions_bp_invalid_fields:" + ",".join(sorted(invalid_central))
        return None

    if task_type == CATEDRALX_ECONOMIC_PROPOSAL_REVIEW:
        required_sections = {"measurement", "assessment", "proposal", "authorization", "execution_boundary"}
        missing_sections = sorted(required_sections - set(payload))
        if payload.get("origin") != "CatedralX.FEX.GOVERNANCE.economic_cycle":
            return "proposal_origin_not_canonical"
        if not isinstance(payload.get("cycle_id"), str) or not payload["cycle_id"].strip():
            return "proposal_cycle_id_missing"
        if missing_sections:
            return "proposal_missing_sections:" + ",".join(missing_sections)
        if any(not isinstance(payload.get(section), dict) for section in required_sections):
            return "proposal_sections_must_be_objects"
        authorization = payload["authorization"]
        if authorization.get("approved") is not True:
            return "human_authorization_missing"
        if authorization.get("identity_verified") is not True:
            return "human_identity_verification_missing"
        if authorization.get("identity_verification") != "APPROVER_IDENTITY_VERIFIER":
            return "human_identity_verifier_not_canonical"
        permissions = authorization.get("identity_permissions")
        if not isinstance(permissions, list) or "economic_cycle_authorize" not in permissions:
            return "human_identity_permission_missing"
        ingress = payload.get("_agents_ingress")
        if not isinstance(ingress, dict) or ingress != {
            "subject": "catedralx-service",
            "scope": "orchestration:run",
            "verified": True,
        }:
            return "trusted_catedralx_service_ingress_required"
        return None

    return None
