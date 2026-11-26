"""Contract tests for the versioned Acceptance envelope and cross-repo trace fixture."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "acceptance_decision.schema.json"
FIXTURES = ROOT / "contracts" / "fixtures"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema():
    value = _load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(value)
    return value


@pytest.mark.parametrize(
    "fixture_name",
    ["acceptance_accepted.valid.json", "acceptance_rejected.valid.json"],
)
def test_acceptance_fixtures_match_schema(schema, fixture_name):
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(_load_json(FIXTURES / fixture_name)), key=lambda e: list(e.path))
    assert errors == []


def test_acceptance_schema_matches_runtime_canonical_field_contract(schema):
    from api.crypto_signer import _CANONICAL_FIELDS
    from verifier.independent_acceptance_verifier import CANONICAL_FIELDS

    assert tuple(_CANONICAL_FIELDS) == tuple(CANONICAL_FIELDS)
    assert set(schema["required"]) == set(CANONICAL_FIELDS) | {"signature"}
    assert set(schema["properties"]) == set(CANONICAL_FIELDS) | {"signature"}


@pytest.mark.parametrize(
    ("fixture_name", "mutate"),
    [
        ("acceptance_accepted.valid.json", lambda d: d.update(extra_field=True)),
        ("acceptance_accepted.valid.json", lambda d: d.update(trace_id="0" * 32)),
        ("acceptance_accepted.valid.json", lambda d: d.update(evidence_digest="bad")),
        ("acceptance_accepted.valid.json", lambda d: d.update(signature="00")),
        ("acceptance_accepted.valid.json", lambda d: d.update(criteria_checked=[])),
        ("acceptance_accepted.valid.json", lambda d: d.update(reasons=["not allowed for accepted"])),
        ("acceptance_rejected.valid.json", lambda d: d.update(reasons=[], missing_evidence=[])),
    ],
)
def test_acceptance_schema_rejects_invalid_contract(schema, fixture_name, mutate):
    decision = _load_json(FIXTURES / fixture_name)
    mutate(decision)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(decision))


def test_decision_result_fixture_preserves_trace_and_non_execution_invariants():
    result = _load_json(FIXTURES / "catedralx_decision_result.valid.json")
    accepted = _load_json(FIXTURES / "acceptance_accepted.valid.json")

    assert result["trace_id"] == accepted["trace_id"]
    assert result["analysis_only"] is True
    assert result["orders_created"] is False
    assert result["orders_executed"] is False
