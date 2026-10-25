import json

from pipeline.lead_classifier import classify_lead


def test_basic(tmp_path, monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_LEADS_PATH", str(tmp_path / "leads.jsonl"))
    r = classify_lead(lead_name="Ana", email="ana@example.com", tipo="mapa_srap",
                     message="Necesito el Mapa SRAP para decidir.")
    assert r["status"] == "ok"
    assert r["classification"]["propuesta_sugerida"] == "Mapa SRAP"
    assert r["classification"]["urgencia"] == "baja"
    assert r["response_sent"] is False
    assert r["orders_created"] is False


def test_urgency_high(tmp_path, monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_LEADS_PATH", str(tmp_path / "leads.jsonl"))
    r = classify_lead(lead_name="Luis", email="l@example.com", tipo="decodificar",
                     message="Es urgente, necesito presupuesto esta semana.")
    assert r["classification"]["urgencia"] == "alta"


def test_taller(tmp_path, monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_LEADS_PATH", str(tmp_path / "leads.jsonl"))
    r = classify_lead(lead_name="Rosa", email="r@example.com", tipo="taller_srap",
                     message="Quiero un taller de 6 sombreros.")
    assert r["classification"]["propuesta_sugerida"] == "Talleres SRAP y 6 Sombreros"


def test_invalid_email(tmp_path, monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_LEADS_PATH", str(tmp_path / "leads.jsonl"))
    r = classify_lead(lead_name="X", email="no-email", tipo="otro", message="hola")
    assert r["status"] == "error"
    assert "email" in r["error"].lower()


def test_invalid_tipo(tmp_path, monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_LEADS_PATH", str(tmp_path / "leads.jsonl"))
    r = classify_lead(lead_name="X", email="x@example.com", tipo="inventado", message="hola")
    assert r["status"] == "error"


def test_appends_jsonl(tmp_path, monkeypatch):
    p = tmp_path / "leads.jsonl"
    monkeypatch.setenv("CHALAMANDRA_LEADS_PATH", str(p))
    classify_lead(lead_name="A", email="a@x.com", tipo="otro", message="uno")
    classify_lead(lead_name="B", email="b@x.com", tipo="otro", message="dos")
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["name"] == "A"
    assert json.loads(lines[1])["name"] == "B"
