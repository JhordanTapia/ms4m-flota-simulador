from datetime import datetime, timedelta, timezone
import pytest

from app.network import RoadNetwork
from app.simulation import SimulationManager
from test_network import sample_network


def connected_network_with_many_pairs():
    # Add more connected pairs so the five trucks have unique deterministic assignments.
    data = sample_network()
    data["Load"] = [{"id": i, "name": f"Load {i}", "coor": [-15.0, -75.0]} for i in range(1, 7)]
    data["Dump"] = [{"id": i + 10, "name": f"Dump {i}", "coor": [-15.0, -74.999]} for i in range(1, 7)]
    return RoadNetwork(data)


def test_simulation_starts_five_stable_trucks_and_is_reproducible():
    network = connected_network_with_many_pairs()
    manager = SimulationManager(network, seed=3811)
    replay = SimulationManager(network, seed=3811)
    result = manager.start()
    replay_result = replay.start()
    assert [truck["id"] for truck in result["trucks"]] == [f"C8Q1-{i:03d}" for i in range(1, 6)]
    assert all(30 <= truck["speed_kmh"] <= 60 for truck in result["trucks"])
    signature = lambda value: [(t["origin"]["id"], t["destination"]["id"], t["speed_kmh"]) for t in value["trucks"]]
    assert signature(result) == signature(replay_result)
    restored = replay.current(result["seed"], result["started_at"])
    assert restored["id"] == result["id"]
    assert signature(restored) == signature(result)


def test_position_interpolates_inside_segments_and_stops_at_destination():
    truck = {"route": [[-15.0, -75.0], [-15.0, -74.999], [-14.999, -74.999]],
             "cumulative_m": [0.0, 100.0, 200.0]}
    assert SimulationManager._position_at(truck, 50) == pytest.approx([-15.0, -74.9995])
    assert SimulationManager._position_at(truck, 150) == pytest.approx([-14.9995, -74.999])
    assert SimulationManager._position_at(truck, 250) == truck["route"][-1]


def test_stateless_simulation_finishes_on_road_destination():
    network = RoadNetwork({
        "Routes": [{"id_trm_cs": 1, "points": [[0, 0], [0, 0.0000001]]}],
        "Load": [{"id": 1, "name": "Origen", "coor": [0, 0]}],
        "Dump": [{"id": 2, "name": "Destino", "coor": [0, 0.0000001]}],
    })
    manager = SimulationManager(network, seed=9, interval_seconds=0.01)
    started_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    state = manager.current(9, started_at)
    assert state["status"] == "finalizada"
    assert all(truck["status"] == "finalizado" for truck in state["trucks"])
    assert all(truck["position"] == [0.0, 0.0000001] for truck in state["trucks"])


def test_elapsed_distance_uses_speed_from_the_previous_interval():
    manager = SimulationManager(connected_network_with_many_pairs(), seed=3811, interval_seconds=0.03)
    started = manager.start()
    started_at = datetime.fromisoformat(started["started_at"])
    previous_speed = started["trucks"][0]["speed_kmh"]
    state = manager._build(3811, started_at, started_at + timedelta(seconds=0.03))
    current = state["trucks"][0]
    expected_distance = previous_speed / 3.6 * 0.03
    assert current["progress_m"] == pytest.approx(expected_distance)
    assert 30 <= current["speed_kmh"] <= 60


def test_report_uses_arithmetic_samples_and_configured_heuristics():
    manager = SimulationManager(connected_network_with_many_pairs(), few_samples_threshold=3,
                                high_speed_warning_percent=25, difference_threshold_kmh=2)

    started = manager.start()
    report = manager.report(started["seed"], started["started_at"])
    first, second = report["trucks"][:2]
    assert first["average_speed_kmh"] == started["trucks"][0]["speed_kmh"]
    assert first["above_55_kmh"] in (0, 1)
    assert first["above_55_percent"] in (0, 100)
    assert second["average_speed_kmh"] == started["trucks"][1]["speed_kmh"]
    assert "promedio de la flota" in report["explanation"]


def test_high_speed_warning_compares_unrounded_share_to_threshold():
    manager = SimulationManager(connected_network_with_many_pairs(), high_speed_warning_percent=25)
    rows = [{"truck_id": "C8Q1-001", "samples": 10_000, "above_55_kmh": 2_501,
             "above_55_percent": 25.0}]
    explanation = manager._explain(rows, [("C8Q1-001", 50.0)])
    assert "Más del 25 %" in explanation

