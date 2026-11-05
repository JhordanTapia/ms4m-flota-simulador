import asyncio
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

    async def start_both():
        first = await manager.start()
        second = await replay.start()
        for instance in (manager, replay):
            if instance._task:
                instance._task.cancel()
                try:
                    await instance._task
                except asyncio.CancelledError:
                    pass
        return first, second

    result, replay_result = asyncio.run(start_both())
    assert [truck["id"] for truck in result["trucks"]] == [f"C8Q1-{i:03d}" for i in range(1, 6)]
    assert all(30 <= truck["speed_kmh"] <= 60 for truck in result["trucks"])
    signature = lambda value: [(t["origin"]["id"], t["destination"]["id"], t["speed_kmh"]) for t in value["trucks"]]
    assert signature(result) == signature(replay_result)


def test_position_interpolates_inside_segments_and_stops_at_destination():
    truck = {"route": [[-15.0, -75.0], [-15.0, -74.999], [-14.999, -74.999]],
             "cumulative_m": [0.0, 100.0, 200.0]}
    assert SimulationManager._position_at(truck, 50) == pytest.approx([-15.0, -74.9995])
    assert SimulationManager._position_at(truck, 150) == pytest.approx([-14.9995, -74.999])
    assert SimulationManager._position_at(truck, 250) == truck["route"][-1]


def test_background_simulation_finishes_on_road_destination():
    network = RoadNetwork({
        "Routes": [{"id_trm_cs": 1, "points": [[0, 0], [0, 0.0000001]]}],
        "Load": [{"id": 1, "name": "Origen", "coor": [0, 0]}],
        "Dump": [{"id": 2, "name": "Destino", "coor": [0, 0.0000001]}],
    })
    manager = SimulationManager(network, seed=9, interval_seconds=0.01)

    async def exercise():
        await manager.start()
        await asyncio.sleep(0.05)
        state = manager.current()
        if manager._task:
            await manager._task
        return state

    state = asyncio.run(exercise())
    assert state["status"] == "finalizada"
    assert all(truck["status"] == "finalizado" for truck in state["trucks"])
    assert all(truck["position"] == [0.0, 0.0000001] for truck in state["trucks"])


def test_elapsed_distance_uses_speed_from_the_previous_interval():
    manager = SimulationManager(connected_network_with_many_pairs(), seed=3811, interval_seconds=0.03)

    class NextSpeed:
        @staticmethod
        def uniform(_minimum, maximum):
            return maximum

    async def exercise():
        await manager.start()
        first = manager.simulation["trucks"][0]
        previous_speed = first["speed_kmh"]
        previous_tick = manager.simulation["_last_tick"]
        manager._rng = NextSpeed()
        deadline = asyncio.get_running_loop().time() + 1
        while manager.simulation["_last_tick"] == previous_tick:
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("La simulación no ejecutó el siguiente intervalo.")
            await asyncio.sleep(0.001)
        elapsed = manager.simulation["_last_tick"] - previous_tick
        current = manager.simulation["trucks"][0]
        expected_distance = previous_speed / 3.6 * elapsed
        assert current["progress_m"] == pytest.approx(expected_distance)
        assert current["speed_kmh"] == 60
        if manager._task:
            manager._task.cancel()
            try:
                await manager._task
            except asyncio.CancelledError:
                pass

    asyncio.run(exercise())


def test_report_uses_arithmetic_samples_and_configured_heuristics():
    manager = SimulationManager(connected_network_with_many_pairs(), few_samples_threshold=3,
                                high_speed_warning_percent=25, difference_threshold_kmh=2)

    async def start_and_stop():
        await manager.start()
        if manager._task:
            manager._task.cancel()
            try:
                await manager._task
            except asyncio.CancelledError:
                pass

    asyncio.run(start_and_stop())
    values = [[60, 40], [30, 30, 30], [50, 50, 50], [50, 50, 50], [50, 50, 50]]
    for truck, speeds in zip(manager.simulation["trucks"], values):
        truck["samples"] = [{"speed_kmh": speed} for speed in speeds]
    report = manager.report()
    first, second = report["trucks"][:2]
    assert first["average_speed_kmh"] == 50
    assert first["above_55_kmh"] == 1
    assert first["above_55_percent"] == 50
    assert second["average_speed_kmh"] == 30
    assert "Más del 25 %" in report["explanation"]
    assert "promedio de la flota" in report["explanation"]


def test_high_speed_warning_compares_unrounded_share_to_threshold():
    manager = SimulationManager(connected_network_with_many_pairs(), high_speed_warning_percent=25)
    rows = [{"truck_id": "C8Q1-001", "samples": 10_000, "above_55_kmh": 2_501,
             "above_55_percent": 25.0}]
    explanation = manager._explain(rows, [("C8Q1-001", 50.0)])
    assert "Más del 25 %" in explanation

