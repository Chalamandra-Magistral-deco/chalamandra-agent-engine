# Agents contract fixtures

This directory contains versioned examples for the Acceptance envelope and a cross-repository
DecisionResult trace fixture.

- `acceptance_decision.schema.json` describes the complete Agents Acceptance envelope,
  including the detached Ed25519 `signature` field.
- `fixtures/acceptance_accepted.valid.json` and
  `fixtures/acceptance_rejected.valid.json` are **shape fixtures only**. Their signatures
  are zero/one-filled placeholders and are deliberately not cryptographically valid.
- `fixtures/catedralx_decision_result.valid.json` mirrors the current required shape of
  CatedralX's `elite/contracts/schemas/decision_result.json`. It is not a second copy of
  that schema and must be validated against the canonical schema in CatedralX.
- The shared trace fixture demonstrates trace continuity only. It does not assert a
  `result_id → candidate_id` mapping; that binding is not part of the current ten-field
  Acceptance signature contract.

Run contract tests with the development extra installed:

```bash
python -m pip install -e '.[dev]'
pytest -q tests/test_acceptance_contract_schema.py
```

The JSON Schema validates structure and declared conditional rules. Runtime checks in
`verifier/independent_acceptance_verifier.py` and `api/acceptance_store.py` remain
authoritative for verifier identity, operator confirmation, signature verification,
candidate lookup, evidence digest recomputation, and persistence/idempotency rules.
