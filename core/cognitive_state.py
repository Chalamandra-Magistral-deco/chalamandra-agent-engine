"""Estado cognitivo computacional; no representa fisiología ni opera órdenes financieras.

Todas las variables se normalizan a [0, 1]. El controlador es determinista:
las mismas entradas producen el mismo estado, sin llamadas a proveedores externos.
"""
from __future__ import annotations
from dataclasses import dataclass
import math


def _unit(name: str, value: float) -> float:
    """Valida un número finito y lo limita al intervalo [0, 1]."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} debe ser un número finito entre 0 y 1") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} debe ser un número finito entre 0 y 1")
    return min(1.0, max(0.0, number))


@dataclass(frozen=True, slots=True)
class CognitiveInputs:
    """Entradas normalizadas por los módulos propietarios de cada dominio."""
    attention: float = 0.5
    confidence: float = 0.5
    uncertainty: float = 0.5
    energy: float = 1.0
    stress: float = 0.0
    curiosity: float = 0.5
    prediction: float | None = None
    actual: float | None = None
    memory_reliability: float = 0.5


@dataclass(frozen=True, slots=True)
class CognitiveState:
    """Instantánea inmutable que otros módulos pueden leer, pero no mutar."""
    attention: float
    confidence: float
    uncertainty: float
    energy: float
    stress: float
    curiosity: float
    prediction_error: float
    learning_rate: float
    risk_multiplier: float
    exploration: float
    status: str

    def to_dict(self) -> dict[str, float | str]:
        """Serializa el estado para trazas, API o telemetría."""
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


class CognitiveController:
    """Deriva controles cognitivos sin ejecutar acciones ni alterar capital."""
    def __init__(self, *, base_learning_rate: float = 0.1, epsilon: float = 1e-9) -> None:
        """
        Configura la tasa de aprendizaje y la tolerancia numérica del controlador.

        Args:
            base_learning_rate (float): Tasa base que debe pasar la validación de unidad
                implementada por `_unit`.
            epsilon (float): Tolerancia estrictamente positiva y finita para operaciones
                numéricas sensibles; por defecto `1e-9`.

        Returns:
            None.

        Raises:
            ValueError: Si `base_learning_rate` incumple `_unit` o si `epsilon` no es
            finito o es menor o igual que cero.

        Side effects:
            Guarda parámetros normalizados en la instancia; no persiste estado.

        Example:
            controller = CognitiveController(base_learning_rate=0.1, epsilon=1e-9)
        """

        self.base_learning_rate = _unit("base_learning_rate", base_learning_rate)
        if not math.isfinite(epsilon) or epsilon <= 0:
            raise ValueError("epsilon debe ser positivo y finito")
        self.epsilon = float(epsilon)

    def update(self, inputs: CognitiveInputs) -> CognitiveState:
        """Calcula una instantánea; error solo se calcula si existe resultado real."""
        attention = _unit("attention", inputs.attention)
        confidence = _unit("confidence", inputs.confidence)
        uncertainty = _unit("uncertainty", inputs.uncertainty)
        energy = _unit("energy", inputs.energy)
        stress = _unit("stress", inputs.stress)
        curiosity = _unit("curiosity", inputs.curiosity)
        reliability = _unit("memory_reliability", inputs.memory_reliability)
        prediction_error = 0.0
        if inputs.prediction is not None and inputs.actual is not None:
            prediction, actual = float(inputs.prediction), float(inputs.actual)
            if not math.isfinite(prediction) or not math.isfinite(actual):
                raise ValueError("prediction y actual deben ser finitos")
            prediction_error = min(1.0, abs(prediction - actual) / (abs(actual) + self.epsilon))
        learning_rate = self.base_learning_rate * (
            0.5 + 0.5 * prediction_error + 0.5 * uncertainty
        ) * (0.5 + 0.5 * energy)
        learning_rate = _unit("learning_rate", learning_rate)
        risk_multiplier = (1.0 - 0.65 * stress) * (1.0 - 0.60 * uncertainty) * energy
        risk_multiplier = _unit("risk_multiplier", risk_multiplier)
        exploration = _unit("exploration", curiosity * uncertainty * (1.0 - confidence))
        adjusted_confidence = _unit(
            "confidence", confidence * (1.0 - 0.5 * uncertainty) * (0.5 + 0.5 * reliability)
        )
        status = "conservative" if stress >= 0.75 or uncertainty >= 0.8 or energy <= 0.2 else "normal"
        return CognitiveState(
            attention, adjusted_confidence, uncertainty, energy, stress, curiosity,
            prediction_error, learning_rate, risk_multiplier, exploration, status,
        )
