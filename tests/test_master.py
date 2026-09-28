"""Smoke test del Control Plane Master."""

import asyncio

from core.master import Master


async def main() -> None:
    master = Master()

    print("===== MASTER INSPECT =====")

    state = master.inspect()

    print("MODELS:", state["models"])
    print("TOOLS:", state["tools"])
    print("CAPABILITIES:", state["capabilities"])

    assert "ollama" in state["models"]
    assert "dynamics" in state["tools"]

    print()
    print("===== ROUTE REASONING =====")

    route = master.route_skill("reasoning")

    print("MODELS:", route["models"])
    print("TOOLS:", route["tools"])

    assert "ollama" in route["models"]
    assert "dynamics" in route["tools"]

    print()
    print("===== EXECUTE DYNAMICS TOOL =====")

    result = await master.execute_tool(
        "dynamics",
        a=1.0,
        b=1.0,
        alpha=2.0,
        gamma=-1.0,
        tau=0.82,
        duration=10.0,
        dt=0.001,
    )

    print("STATUS:", result["status"])
    print("KEYS:", list(result.keys()))

    assert result["status"] == "ok"
    assert "equilibrium" in result
    assert "hopf" in result
    assert "analysis" in result

    print()
    print("TEST MASTER OK")


if __name__ == "__main__":
    asyncio.run(main())
