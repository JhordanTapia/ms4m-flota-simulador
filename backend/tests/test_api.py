import pytest
from fastapi.testclient import TestClient

from app import main
from app.network import NetworkDataError, RoadNetwork
from app.simulation import SimulationManager


def test_api_network_start_current_report_and_request_validation():
    with TestClient(main.app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["network"]["route_count"] == 553
        assert len(client.get("/api/routes").json()) == 553
        locations = client.get("/api/locations").json()
        assert len(locations["loads"]) == 30
        assert len(locations["dumps"]) == 139

        invalid = client.post("/api/simulations", json={"seed": "not-an-integer"})
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["error"] == "validation_error"

        started = client.post("/api/simulations", json={"seed": 3811})
        assert started.status_code == 201
        assert len(started.json()["trucks"]) == 5
        assert all(len(truck["route"]) >= 2 for truck in started.json()["trucks"])
        assert started.json()["route_selection"]["reachable_pairs"] == 4002
        assert started.json()["route_selection"]["unreachable_pairs_skipped"] == 168

        run_params = {"seed": started.json()["seed"], "started_at": started.json()["started_at"]}
        current = client.get("/api/simulations/current", params=run_params).json()
        assert len(current["trucks"]) == 5
        assert all("route" not in truck for truck in current["trucks"])
        assert current["route_selection"]["unreachable_pairs_skipped"] == 168
        report = client.get("/api/simulations/report", params=run_params).json()
        assert len(report["trucks"]) == 5
        assert "heuristic_thresholds" in report
        snapshot = client.get("/api/simulations/snapshot", params=run_params).json()
        assert len(snapshot["simulation"]["trucks"]) == 5
        assert len(snapshot["report"]["trucks"]) == 5
        assert client.get("/api/docs").status_code == 200
        assert client.get("/api/openapi.json").status_code == 200


def test_invalid_startup_data_returns_consistent_service_unavailable(monkeypatch):
    monkeypatch.setattr(main, "network", None)
    monkeypatch.setattr(main, "simulations", None)
    monkeypatch.setattr(main, "startup_error", "El JSON no contiene una red válida.")
    with TestClient(main.app) as client:
        health = client.get("/api/health")
        routes = client.get("/api/network")
    assert health.status_code == routes.status_code == 503
    assert health.json()["detail"] == routes.json()["detail"]
    assert health.json()["detail"]["error"] == "invalid_network_data"


def test_start_rejects_origins_and_destinations_in_different_components(monkeypatch):
    data = {
        "Routes": [
            {"id_trm_cs": 1, "points": [[0, 0], [0, 0.001]]},
            {"id_trm_cs": 2, "points": [[1, 1], [1, 1.001]]},
        ],
        "Load": [{"id": 1, "name": "Origen", "coor": [0, 0]}],
        "Dump": [{"id": 2, "name": "Destino", "coor": [1, 1.001]}],
    }
    network = RoadNetwork(data)
    monkeypatch.setattr(main, "network", network)
    monkeypatch.setattr(main, "simulations", SimulationManager(network))
    with TestClient(main.app) as client:
        response = client.post("/api/simulations", json={"seed": 3811})
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "unreachable_network"


def test_invalid_interval_configuration_is_rejected(monkeypatch):
    monkeypatch.setenv("SIMULATION_INTERVAL_SECONDS", "0")
    with pytest.raises(NetworkDataError, match="SIMULATION_INTERVAL_SECONDS"):
        main.load_services()
