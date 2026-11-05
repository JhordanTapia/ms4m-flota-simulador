import pytest

from app.network import NetworkDataError, RoadNetwork


def sample_network():
    return {
        "Routes": [
            {"id_trm_cs": 20, "nombre_tramo": "higher", "color": "#0000FF", "points": [[-15.0, -75.0], [-15.0, -74.999]]},
            {"id_trm_cs": 10, "nombre_tramo": "lower", "color": "#00FF00", "points": [[-15.0, -75.0], [-15.0, -74.999]]},
            {"id_trm_cs": 30, "nombre_tramo": "isolated", "color": "#FF0000", "points": [[-16.0, -76.0], [-16.0, -75.999]]},
        ],
        "Load": [{"id": 1, "name": "Origen", "coor": [-15.0, -75.0], "radio": 10}],
        "Dump": [{"id": 2, "name": "Destino", "coor": [-15.0, -74.999], "radio": None}],
    }


def test_dijkstra_uses_distance_and_route_id_tie_break():
    network = RoadNetwork(sample_network())
    result = network.shortest_path(network.load_nodes[1], network.dump_nodes[2])
    assert result is not None
    assert result["route_ids"] == [10]
    assert result["distance_m"] > 0


def test_components_filter_unreachable_pairs():
    network = RoadNetwork(sample_network())
    assert network.component_count == 2
    assert len(network.reachable_pairs()) == 1


def test_malformed_route_is_skipped_with_warning():
    data = sample_network()
    data["Routes"].append({"id_trm_cs": 40, "points": [[100, 0], [101, 0]]})
    network = RoadNetwork(data)
    assert network.validation["invalid_routes"] == 1
    assert any("geometría incompleta" in warning for warning in network.validation["warnings"])


def test_malformed_json_file_returns_a_clear_data_error(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(NetworkDataError, match="No se pudo leer el JSON"):
        RoadNetwork.from_file(path)


def test_invalid_colors_and_radii_are_reported_without_mutating_input():
    data = sample_network()
    data["Routes"][0]["color"] = "blue"
    data["Load"][0]["radio"] = -10
    network = RoadNetwork(data)
    assert network.routes[0]["color"] == "#64748b"
    assert data["Routes"][0]["color"] == "blue"
    assert data["Load"][0]["radio"] == -10
    assert any("color hexadecimal" in message for message in network.validation["warnings"])
    assert any("radio inválido" in message for message in network.validation["warnings"])

