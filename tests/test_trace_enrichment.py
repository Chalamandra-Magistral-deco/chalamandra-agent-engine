"""C-6 (liberación): traces.jsonl registra candidate_id y decision_id.

Antes, reconstruir una traza exigía cruzar traces.jsonl con el candidate
store, el acceptance store y el TaskBus. Ahora el handler anota en la traza
el candidato y la decisión que sirvió la petición, cuando los conoce.
La unicidad estricta del trace_id NO se impone: los reintentos y replays
idempotentes reutilizan legítimamente la misma traza (ver comentario en
server.py:_get_trace_id).
"""
import json

from api import trace_store
from api.server import EngineRequestHandler


def _read_traces(tmp_path):
    path = tmp_path / "traces.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_record_request_persists_candidate_and_decision_ids(tmp_path):
    trace_store.record_request(
        trace_id="1234567890abcdef1234567890abcdef",
        traceparent=None,
        method="POST",
        path="/v1/acceptance",
        status=201,
        client_ip="127.0.0.1",
        candidate_id="cand-1",
        decision_id="dec-1",
    )
    records = _read_traces(tmp_path)
    assert len(records) == 1
    assert records[0]["candidate_id"] == "cand-1"
    assert records[0]["decision_id"] == "dec-1"
    assert records[0]["trace_id"] == "1234567890abcdef1234567890abcdef"


def test_record_request_without_ids_keeps_previous_shape(tmp_path):
    trace_store.record_request(
        trace_id="1234567890abcdef1234567890abcdef",
        traceparent=None,
        method="GET",
        path="/healthz",
        status=200,
        client_ip="127.0.0.1",
    )
    records = _read_traces(tmp_path)
    assert len(records) == 1
    assert "candidate_id" not in records[0]
    assert "decision_id" not in records[0]


def test_log_message_forwards_handler_candidate_and_decision(tmp_path):
    handler = EngineRequestHandler.__new__(EngineRequestHandler)
    handler.client_address = ("127.0.0.1", 1234)
    handler.path = "/v1/acceptance"
    handler.headers = {}
    handler.command = "POST"
    handler.trace_id = "1234567890abcdef1234567890abcdef"
    handler.trace_candidate_id = "cand-9"
    handler.trace_decision_id = "dec-9"

    handler.log_message('"%s" %s', "POST /v1/acceptance HTTP/1.1", "201")

    records = _read_traces(tmp_path)
    assert len(records) == 1
    assert records[0]["candidate_id"] == "cand-9"
    assert records[0]["decision_id"] == "dec-9"
    assert records[0]["status"] == 201


def test_log_message_without_ids_records_no_candidate_fields(tmp_path):
    handler = EngineRequestHandler.__new__(EngineRequestHandler)
    handler.client_address = ("127.0.0.1", 1234)
    handler.path = "/healthz"
    handler.headers = {}
    handler.command = "GET"
    handler.trace_id = "1234567890abcdef1234567890abcdef"

    handler.log_message('"%s" %s', "GET /healthz HTTP/1.1", "200")

    records = _read_traces(tmp_path)
    assert len(records) == 1
    assert "candidate_id" not in records[0]
    assert "decision_id" not in records[0]
