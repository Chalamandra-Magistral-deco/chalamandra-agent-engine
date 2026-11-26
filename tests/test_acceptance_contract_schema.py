"""Contract tests for the published independent-acceptance schema and signer."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from verifier.independent_acceptance_verifier import validate_decision

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "acceptance" / "acceptance-decision.schema.json"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "acceptance"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def unsigned_payload(signed: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in signed.items() if key != "signature"}


def test_acceptance_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("fixture_name", ["accepted.json", "rejected.json"])
def test_signed_fixtures_pass_schema_and_signer_contract(fixture_name: str) -> None:
    signed = load_fixture(fixture_name)

    # The schema validates the complete wire record, including signature shape.
    VALIDATOR.validate(signed)

    # The isolated signer validates only the ten canonical fields; it does not
    # consume or validate the signature that it is responsible for producing.
    unsigned = unsigned_payload(signed)
    assert validate_decision(unsigned, verifier_id=signed["verifier_id"]) == unsigned


def mutate_extra_property(record: dict[str, Any]) -> None:
    record["unexpected"] = "not-allowed"


def mutate_digest(record: dict[str, Any]) -> None:
    record["evidence_digest"] = "not-a-sha256-digest"


def mutate_zero_trace_id(record: dict[str, Any]) -> None:
    record["trace_id"] = "0" * 32


def mutate_accepted_with_reason(record: dict[str, Any]) -> None:
    record["reasons"] = ["accepted decisions cannot carry rejection reasons"]


def mutate_rejected_without_explanation(record: dict[str, Any]) -> None:
    record["reasons"] = []
    record["missing_evidence"] = []


def mutate_non_rfc3339_timestamp(record: dict[str, Any]) -> None:
    record["decided_at"] = "2026-11-26 12:00:00+00:00"


@pytest.mark.parametrize(
    ("fixture_name", "mutate"),
    [
        ("accepted.json", mutate_extra_property),
        ("accepted.json", mutate_digest),
        ("accepted.json", mutate_zero_trace_id),
        ("accepted.json", mutate_accepted_with_reason),
        ("rejected.json", mutate_rejected_without_explanation),
        ("accepted.json", mutate_non_rfc3339_timestamp),
    ],
    ids=[
        "reject-extra-property",
        "reject-malformed-digest",
        "reject-zero-trace-id",
        "reject-accepted-with-reason",
        "reject-rejected-without-explanation",
        "reject-non-rfc3339-date-time",
    ],
)
def test_schema_and_signer_reject_invalid_contracts(
    fixture_name: str, mutate: Callable[[dict[str, Any]], None]
) -> None:
    signed = copy.deepcopy(load_fixture(fixture_name))
    mutate(signed)

    with pytest.raises(ValidationError):
        VALIDATOR.validate(signed)

    unsigned = unsigned_payload(signed)
    with pytest.raises(ValueError):
        validate_decision(unsigned, verifier_id=signed["verifier_id"])


def test_schema_rejects_malformed_ed25519_signature_shape() -> None:
    signed = load_fixture("accepted.json")
    signed["signature"] = "0" * 126

    with pytest.raises(ValidationError):
        VALIDATOR.validate(signed)
