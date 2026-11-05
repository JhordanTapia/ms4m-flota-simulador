import asyncio

from app.network import RoadNetwork
from app.simulation import SimulationManager
from test_network import sample_network


def test_simulation_starts_five_stable_trucks_and_report_is_grounded():
    network = RoadNetwork(sample_network())
    # Add more connected pairs so the five trucks have unique deterministic assignments.
    data = sample_network()
    data["Load"] = [{"id": i, "name": f"Load {i}", "coor": [-15.0, -75.0]} for i in range(1, 7)]
    data["Dump"] = [{"id": i + 10, "name": f"Dump {i}", "coor": [-15.0, -74.999]} for i in range(1, 7)]
    network = RoadNetwork(data)
    manager = SimulationManager(network, seed=3811)
    result = asyncio.run(manager.start())
    assert [truck["id"] for truck in result["trucks"]] == [f"C8Q1-{i:03d}" for i in range(1, 6)]
    assert all(30 <= truck["speed_kmh"] <= 60 for truck in result["trucks"])
    report = manager.report()
    assert len(report["trucks"]) == 5
    assert all(row["samples"] == 1 for row in report["trucks"])
    assert "3811" not in report["explanation"]
    if manager._task:
        manager._task.cancel()

