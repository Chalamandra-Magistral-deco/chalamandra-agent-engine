import math
from dynamics.stability import hopf_boundary
from dynamics.model import CoupledDelayModel
from dynamics.analyze import analyze_trajectory

def test_hopf():
    tau, omega = hopf_boundary(1.0, 1.0, -2.0)
    assert abs(omega - 1.0) < 1e-9
    assert abs(tau - math.pi / 4.0) < 1e-9

def test_regime():
    m = CoupledDelayModel(a=1, b=1, alpha=2, gamma=-1, tau=0.82, K=1.0)
    t = m.simulate(duration=160.0, dt=0.0005)
    r = analyze_trajectory(t)
    assert r["bio"]["regime"] == "GROWING"

if __name__ == "__main__":
    test_hopf()
    print("OK hopf")
    test_regime()
    print("OK regime")
    print("\nTESTS OK")


def test_simulation_covers_exact_duration_with_step_at_most_dt():
    model = CoupledDelayModel(a=1.0, b=1.0, alpha=0.0, gamma=0.0, tau=0.0)
    trajectory = model.simulate(duration=1.0, dt=0.3)

    assert trajectory.shape == (5, 3)
    assert trajectory[0, 0] == 0.0
    assert math.isclose(trajectory[-1, 0], 1.0, abs_tol=1e-12)
    assert max(trajectory[1:, 0] - trajectory[:-1, 0]) <= 0.3 + 1e-12


def test_fractional_delay_uses_linear_interpolation():
    model = CoupledDelayModel(a=1.0, b=1.0, alpha=0.0, gamma=1.0, tau=0.15)
    trajectory = model.simulate(
        duration=0.3,
        dt=0.1,
        p_bio0=1.0,
        p_sint0=0.0,
    )

    h = 0.1
    y1 = h * math.tanh(1.0)
    y2 = y1 + h * (-y1 + math.tanh(1.0))
    # At t=0.2, t-tau=0.05; P_bio(0.05) is interpolated between 1.0 and 0.9.
    expected_y3 = y2 + h * (-y2 + math.tanh(0.95))
    assert math.isclose(trajectory[3, 2], expected_y3, rel_tol=1e-11, abs_tol=1e-12)


def test_simulation_rejects_non_finite_inputs_and_excessive_steps():
    model = CoupledDelayModel(a=1.0, b=1.0, alpha=0.0, gamma=0.0, tau=0.0)
    import pytest

    with pytest.raises(ValueError, match="duration"):
        model.simulate(duration=float("nan"))
    with pytest.raises(ValueError, match="max_steps"):
        model.simulate(duration=10.0, dt=0.001, max_steps=100)


def test_analysis_rejects_non_monotonic_or_non_finite_trajectory():
    import numpy as np
    import pytest

    with pytest.raises(ValueError, match="estrictamente creciente"):
        analyze_trajectory(np.array([[0.0, 0.0, 0.0], [1.0, 0.1, 0.2], [0.5, 0.2, 0.3]]))
    with pytest.raises(ValueError, match="NaN ni infinitos"):
        analyze_trajectory(np.array([[0.0, 0.0, 0.0], [1.0, np.nan, 0.2], [2.0, 0.2, 0.3]]))


def test_dynamics_tool_rejects_excessive_steps_before_computation(monkeypatch):
    from pipeline import dynamics_tool

    called = []

    def unexpected_calculation(**kwargs):
        called.append(True)
        raise RuntimeError("El cálculo no debería iniciarse.")

    monkeypatch.setattr(
        dynamics_tool, "analyze_equilibrium", unexpected_calculation
    )
    result = dynamics_tool.analyze_dynamics(
        a=1.0, b=1.0, alpha=0.2, gamma=0.1, tau=0.2,
        duration=2.0, dt=0.000001,
    )

    assert result["status"] == "error"
    assert "1000000" in result["error"]
    assert called == []
