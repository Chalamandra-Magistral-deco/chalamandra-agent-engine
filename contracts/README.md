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

The JSON Schema validates structure and declared conditional rules in the contract test suite.
The API acceptance store also now rejects unmodeled envelope fields, empty list items, invalid
accepted/rejected combinations, and malformed Ed25519 signature encodings before candidate
lookup. Runtime checks in `verifier/independent_acceptance_verifier.py` and
`api/acceptance_store.py` remain authoritative for verifier identity, operator confirmation,
signature verification, candidate lookup, evidence digest recomputation, and
persistence/idempotency rules. The schema is not loaded dynamically by the API process.

## Normative references

- [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12/) — schema dialect and validation vocabulary.
- [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032) — EdDSA and Ed25519; an Ed25519 signature is 64 octets (128 hexadecimal characters).
- [W3C Trace Context](https://www.w3.org/TR/trace-context/) — the `trace-id` is 16 bytes / 32 lowercase hexadecimal characters; all-zero is invalid.
- [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785) — JSON Canonicalization Scheme. Agents currently uses its repository-specific `json.dumps` serialization in `api/crypto_signer.py`; this implementation is not claimed to conform to JCS.
