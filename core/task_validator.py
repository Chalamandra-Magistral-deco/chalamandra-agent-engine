"""Validación determinista de resultados de tareas."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    """
    Resultado estructurado de una validación determinista.

    Attributes:
        status (str): Estado `PASS`, `FAIL` o `NOT_EVALUATED`.
        passed (bool | None): `True` si pasa, `False` si falla y `None` si no se evaluó.
        checked (list[str]): Reglas no vacías que se inspeccionaron.
        errors (list[str]): Incumplimientos o reglas no reconocidas.

    Example:
        result = TaskValidator().validate("OK", ["exact:OK"])
        assert result.is_pass
    """

    status: str
    passed: bool | None
    checked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_pass(self) -> bool:
        """
        Indica si el estado de validación es exactamente `PASS`.

        Returns:
            bool: `True` solo para `status == "PASS"`.

        Raises:
            No genera excepciones deliberadas.

        Example:
            if result.is_pass:
                print("La salida pasó las reglas")
        """
        return self.status == "PASS"

    @property
    def is_fail(self) -> bool:
        """
        Indica si el estado de validación es exactamente `FAIL`.

        Returns:
            bool: `True` solo para `status == "FAIL"`; `NOT_EVALUATED` no es un fallo evaluado.

        Raises:
            No genera excepciones deliberadas.

        Example:
            if result.is_fail:
                print(result.errors)
        """
        return self.status == "FAIL"

    def to_dict(self) -> dict:
        """
        Serializa los campos públicos del resultado de validación.

        Returns:
            dict: Estado, valor `passed`, lista de reglas comprobadas y errores.

        Raises:
            No genera excepciones deliberadas.

        Side effects:
            No modifica el objeto; devuelve referencias a las listas almacenadas.

        Example:
            payload = result.to_dict()
        """
        return {
            "status": self.status,
            "passed": self.passed,
            "checked": self.checked,
            "errors": self.errors,
        }


class TaskValidator:
    """
    Validador determinista de reglas explícitas sobre texto de salida.

    Reglas reconocidas: `non_empty`, `exact:<texto>`, `contains:<texto>` y
    `regex:<patrón>`. Las reglas desconocidas y expresiones regulares inválidas
    se registran como errores; no se interpretan como aprobación.

    Example:
        validator = TaskValidator()
        result = validator.validate("OK", ["non_empty", "exact:OK"])
    """

    def validate(
        self,
        output: str,
        rules: list[str] | None = None,
    ) -> ValidationResult:
        """
        Evalúa la salida contra las reglas explícitas suministradas.

        Args:
            output (str): Texto que se debe validar.
            rules (list[str] | None): Lista de reglas admitidas; `None` o una lista vacía
                devuelve `NOT_EVALUATED` (fail-closed), no una aprobación.

        Returns:
            ValidationResult: Estado, reglas comprobadas y errores encontrados.

        Raises:
            Puede propagar errores de tipo si `output` o `rules` no respetan sus tipos
            declarados; los patrones `regex` inválidos se informan en `errors`.

        Side effects:
            Ninguno; no hace red ni persistencia.

        Example:
            result = TaskValidator().validate("resultado", ["non_empty", "contains:ado"])
            assert result.is_pass
        """
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
