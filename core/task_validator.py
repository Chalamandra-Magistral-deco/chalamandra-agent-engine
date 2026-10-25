"""Validación determinista de resultados de tareas."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    """Resultado de validación de una salida."""

    status: str
    passed: bool | None
    checked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_pass(self) -> bool:
        return self.status == "PASS"

    @property
    def is_fail(self) -> bool:
        return self.status == "FAIL"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "passed": self.passed,
            "checked": self.checked,
            "errors": self.errors,
        }


class TaskValidator:
    """Motor pequeño y determinista de validación."""

    def validate(
        self,
        output: str,
        rules: list[str] | None = None,
    ) -> ValidationResult:
        rules = list(rules or [])

        if not rules:
            # Fail-closed: sin reglas, no hay validación.
            # No se puede declarar 'ok' o 'accepted' si no se evaluó.
            return ValidationResult(
                status="NOT_EVALUATED",
                passed=None,
            )

        checked: list[str] = []
        errors: list[str] = []

        for rule in rules:
            rule = str(rule).strip()

            if not rule:
                continue

            checked.append(rule)

            if rule == "non_empty":
                if not output.strip():
                    errors.append("non_empty: salida vacía")
                continue

            if rule.startswith("exact:"):
                expected = rule[len("exact:"):].strip()

                if output.strip() != expected:
                    errors.append(
                        f"exact: esperado={expected!r}, "
                        f"obtenido={output.strip()!r}"
                    )
                continue

            if rule.startswith("contains:"):
                expected = rule[len("contains:"):]

                if expected not in output:
                    errors.append(
                        f"contains: falta={expected!r}"
                    )
                continue

            if rule.startswith("regex:"):
                pattern = rule[len("regex:"):]

                try:
                    matched = re.search(pattern, output)
                except re.error as exc:
                    errors.append(
                        f"regex: patrón inválido={exc}"
                    )
                    continue

                if not matched:
                    errors.append(
                        f"regex: no coincide={pattern!r}"
                    )
                continue

            errors.append(f"unknown_rule:{rule}")

        passed = not errors

        return ValidationResult(
            status="PASS" if passed else "FAIL",
            passed=passed,
            checked=checked,
            errors=errors,
        )
