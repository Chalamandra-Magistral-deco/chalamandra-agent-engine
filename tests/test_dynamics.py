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
