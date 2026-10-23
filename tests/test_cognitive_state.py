import math
import pytest
from core.cognitive_state import CognitiveController, CognitiveInputs


def test_update_is_deterministic_and_bounded() -> None:
    controller = CognitiveController()
    inputs = CognitiveInputs(attention=0.8, confidence=0.7, uncertainty=0.3, energy=0.9,
        stress=0.2, curiosity=0.6, prediction=110, actual=100, memory_reliability=0.8)
    first, second = controller.update(inputs), controller.update(inputs)
    assert first == second
    for value in first.to_dict().values():
        if isinstance(value, float):
            assert 0.0 <= value <= 1.0
    assert first.prediction_error == pytest.approx(0.1)


def test_high_stress_selects_conservative_mode() -> None:
    state = CognitiveController().update(CognitiveInputs(stress=0.9, energy=0.9))
    assert state.status == "conservative"
    assert state.risk_multiplier < 1.0


def test_missing_outcome_does_not_invent_prediction_error() -> None:
    state = CognitiveController().update(CognitiveInputs(prediction=12.0, actual=None))
    assert state.prediction_error == 0.0


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_inputs_are_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        CognitiveController().update(CognitiveInputs(stress=value))


def test_invalid_epsilon_is_rejected() -> None:
    with pytest.raises(ValueError):
        CognitiveController(epsilon=0)
