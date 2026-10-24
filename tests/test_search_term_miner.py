from core.master import Master
from pipeline import search_term_miner as miner_module
from pipeline.search_term_miner import mine_search_terms_tool


def test_miner_generates_candidates_and_ngram_report(tmp_path, monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_SEARCH_TERMS_DIR", str(tmp_path))
    report = tmp_path / "report.csv"
    report.write_text(
        "Search term,Cost,Conversions,Conv. value\n"
        '"buy blue shoes",12.50,2,120\n'
        '"free blue shoes",8.00,0,0\n'
        '"where shoes",1.00,0,0\n'
        '"buy green shoes",2.00,1,50\n',
        encoding="utf-8",
    )

    result = mine_search_terms_tool(
        csv_path=str(report),
        profile="direct-response",
        cost_floor=5.0,
        min_conversions=1.0,
    )

    assert result["status"] == "ok"
    assert result["counts"]["rows_processed"] == 4
    assert result["counts"]["add_candidates"] == 2
    assert result["counts"]["negative_candidates"] == 1
    assert result["summary"]["measured_spend"] == 23.5
    assert result["summary"]["measured_waste_spend"] == 8.0
    assert result["maintenance_diff"]["add"][0]["action"] == "ADD_KEYWORD_CANDIDATE"
    assert result["maintenance_diff"]["negate"][0]["match_type"] == "exact"
    assert result["ngram_waste"]
    assert result["roas_s"]["score"] is None


def test_miner_supports_spanish_headers_and_decimal_comma(tmp_path, monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_SEARCH_TERMS_DIR", str(tmp_path))
    report = tmp_path / "reporte.csv"
    report.write_text(
        "Término de búsqueda;Coste;Conversiones\n"
        '"comprar café";1.234,56;1\n',
        encoding="utf-8",
    )

    result = mine_search_terms_tool(
        csv_path=str(report),
        profile="prospecting",
        cost_floor=100.0,
    )

    assert result["status"] == "ok"
    assert result["summary"]["measured_spend"] == 1234.56
    assert result["counts"]["add_candidates"] == 1
    assert result["columns"]["query"] == "Término de búsqueda"


def test_miner_stops_when_conversion_column_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_SEARCH_TERMS_DIR", str(tmp_path))
    report = tmp_path / "missing.csv"
    report.write_text(
        "Search term,Cost\n"
        '"buy blue shoes",12.50\n',
        encoding="utf-8",
    )

    result = mine_search_terms_tool(
        csv_path=str(report),
        profile="direct-response",
        cost_floor=5.0,
    )

    assert result["status"] == "error"
    assert "conversion" in result["error"].lower()


def test_miner_rejects_paths_outside_authorized_directory(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "private.csv"
    outside.write_text(
        "Search term,Cost,Conversions\n"
        '"buy shoes",10,1\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CHALAMANDRA_SEARCH_TERMS_DIR", str(allowed))

    result = mine_search_terms_tool(
        csv_path=str(outside),
        profile="direct-response",
        cost_floor=5.0,
    )

    assert result["status"] == "error"
    assert "autorizado" in result["error"].lower()


def test_master_registers_search_term_miner():
    master = Master()

    assert "search_term_miner" in master.tools.available()
    assert "search_term_miner" in master.route_skill("analysis")["tools"]
    assert "dynamics" in master.route_skill("analysis")["tools"]


def test_miner_rejects_csv_over_size_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_SEARCH_TERMS_DIR", str(tmp_path))
    monkeypatch.setattr(miner_module, "MAX_CSV_BYTES", 32)
    (tmp_path / "large.csv").write_text(
        "Search term,Cost,Conversions\n"
        '"buy blue shoes",12.50,2\n',
        encoding="utf-8",
    )
    result = mine_search_terms_tool(
        csv_path="large.csv", profile="direct-response", cost_floor=5.0
    )
    assert result["status"] == "error"
    assert "tamaño" in result["error"].lower()


def test_miner_rejects_csv_over_row_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("CHALAMANDRA_SEARCH_TERMS_DIR", str(tmp_path))
    monkeypatch.setattr(miner_module, "MAX_CSV_ROWS", 2)
    (tmp_path / "too_many_rows.csv").write_text(
        "Search term,Cost,Conversions\n"
        '"buy blue shoes",12.50,2\n'
        '"free blue shoes",8.00,0\n'
        '"where shoes",1.00,0\n',
        encoding="utf-8",
    )
    result = mine_search_terms_tool(
        csv_path="too_many_rows.csv", profile="direct-response", cost_floor=5.0
    )
    assert result["status"] == "error"
    assert "2 filas" in result["error"].lower()
