from api import jsonl_store


def test_append_and_read_jsonl_preserves_unicode_and_records(tmp_path):
    path = tmp_path / "nested" / "records.jsonl"

    jsonl_store.append_jsonl(path, {"message": "Chalamandra ñ"})
    jsonl_store.append_jsonl(path, {"sequence": 2})

    assert path.read_text(encoding="utf-8") == (
        '{"message": "Chalamandra ñ"}\n'
        '{"sequence": 2}\n'
    )
    assert jsonl_store.read_jsonl(path) == [
        {"message": "Chalamandra ñ"},
        {"sequence": 2},
    ]


def test_read_jsonl_skips_blank_and_malformed_lines(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text('{"ok": true}\n\nnot-json\n{"count": 2}\n', encoding="utf-8")

    assert jsonl_store.read_jsonl(path) == [{"ok": True}, {"count": 2}]


def test_read_jsonl_missing_file_returns_empty_list(tmp_path):
    assert jsonl_store.read_jsonl(tmp_path / "missing.jsonl") == []
