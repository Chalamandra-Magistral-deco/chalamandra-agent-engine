from core.task_validator import TaskValidator


def test_no_rules_is_skipped():
    result = TaskValidator().validate("OK")

    assert result.status == "NOT_EVALUATED"
    assert result.passed is None
    assert not result.errors


def test_non_empty_passes():
    result = TaskValidator().validate(
        "OK",
        ["non_empty"],
    )

    assert result.status == "PASS"
    assert result.passed is True


def test_exact_passes():
    result = TaskValidator().validate(
        "OK",
        ["exact:OK"],
    )

    assert result.status == "PASS"


def test_exact_fails():
    result = TaskValidator().validate(
        "NO",
        ["exact:OK"],
    )

    assert result.status == "FAIL"
    assert result.passed is False
    assert result.errors


def test_contains_passes():
    result = TaskValidator().validate(
        "La respuesta contiene HTML",
        ["contains:HTML"],
    )

    assert result.status == "PASS"


def test_regex_passes():
    result = TaskValidator().validate(
        "resultado-123",
        [r"regex:^resultado-\d+$"],
    )

    assert result.status == "PASS"


def test_unknown_rule_fails():
    result = TaskValidator().validate(
        "OK",
        ["inventada"],
    )

    assert result.status == "FAIL"
    assert "unknown_rule:inventada" in result.errors
