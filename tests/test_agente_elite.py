import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import agente_elite as elite


def test_disk_usage_returns_real_read_only_metrics():
    result = elite.disk_usage_root(elite.EmptyArgs())

    assert result["path"] == "/"
    assert result["total_bytes"] > 0
    assert result["used_bytes"] >= 0
    assert result["free_bytes"] >= 0
    assert 0 <= result["used_percent"] <= 100


@pytest.mark.skipif(
    not elite.REPOS_ROOT.is_dir(),
    reason="requires VM-local repos directory (not present in CI runners)",
)
def test_repository_listing_is_fixed_and_limited():
    result = elite.list_repos(elite.ListReposArgs(limit=10))

    assert result["path"] == str(elite.REPOS_ROOT)
    assert result["count"] <= 10
    assert len(result["entries"]) == result["count"]
    assert all("name" in entry and "type" in entry
               for entry in result["entries"])


def test_repository_listing_rejects_external_paths():
    with pytest.raises(ValidationError):
        elite.ListReposArgs.model_validate({
            "limit": 10,
            "path": "/etc",
        })


def test_repository_listing_rejects_limits_above_ten():
    with pytest.raises(ValidationError):
        elite.ListReposArgs(limit=11)


def test_repository_listing_rejects_zero():
    with pytest.raises(ValidationError):
        elite.ListReposArgs(limit=0)


def test_unknown_tool_is_rejected():
    agent = elite.EliteAgentCore()
    result = agent.execute_tool({
        "function": {
            "name": "sys_execute",
            "arguments": {"command": "id"},
        }
    })

    assert json.loads(result)["error"] == "Herramienta no autorizada"


def test_prototype_contains_no_shell_execution():
    source = Path(elite.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_imports = {"subprocess"}
    forbidden_calls = {"system", "popen", "eval", "exec"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(
                alias.name.split(".")[0] in forbidden_imports
                for alias in node.names
            )

        if isinstance(node, ast.ImportFrom):
            assert node.module not in forbidden_imports

        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            assert name not in forbidden_calls

            assert not any(
                keyword.arg == "shell" and
                isinstance(keyword.value, ast.Constant) and
                keyword.value.value is True
                for keyword in node.keywords
            )
