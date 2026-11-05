from __future__ import annotations

import heapq
import json
import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class NetworkDataError(ValueError):
    pass


def haversine(a: list[float] | tuple[float, float], b: list[float] | tuple[float, float]) -> float:
    radius = 6_371_000.0
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlat = lat2 - lat1
    dlon = math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def node_key(point: list[float] | tuple[float, float]) -> tuple[float, float]:
    return round(float(point[0]), 7), round(float(point[1]), 7)


@dataclass(frozen=True)
class Edge:
    target: tuple[float, float]
    distance: float
    route_id: int


class RoadNetwork:
    """Validated road graph built from consecutive vertices in each route."""

    def __init__(self, data: dict[str, Any]):
        self.routes: list[dict[str, Any]] = []
        self.loads: list[dict[str, Any]] = []
        self.dumps: list[dict[str, Any]] = []
        self.adjacency: dict[tuple[float, float], list[Edge]] = defaultdict(list)
        self.coordinates: dict[tuple[float, float], list[float]] = {}
        self.components: dict[tuple[float, float], int] = {}
        self.validation = {"errors": [], "warnings": [], "valid_routes": 0, "invalid_routes": 0}
        self._load_and_validate(data)
        self._build_components()
        if not self.routes or not self.adjacency:
            raise NetworkDataError("El archivo no contiene tramos transitables válidos.")
        self.load_nodes = self._snap_locations(self.loads, "carga")
        self.dump_nodes = self._snap_locations(self.dumps, "descarga")

    @classmethod
    def from_file(cls, path: str | Path) -> "RoadNetwork":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise NetworkDataError(f"No se pudo leer el JSON de la red: {exc}") from exc
        return cls(data)

    @staticmethod
    def _valid_point(point: Any) -> bool:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return False
        if any(isinstance(value, bool) for value in point):
            return False
        try:
            lat, lon = float(point[0]), float(point[1])
            return math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180
        except (TypeError, ValueError):
            return False

    def _load_and_validate(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise NetworkDataError("La raíz del JSON debe ser un objeto.")
        for key in ("Routes", "Load", "Dump"):
            if not isinstance(data.get(key), list):
                raise NetworkDataError(f"El campo raíz '{key}' debe ser un arreglo.")

        seen_route_ids: set[int] = set()
        for index, raw in enumerate(data["Routes"]):
            if not isinstance(raw, dict):
                self._warn(f"Routes[{index}] no es un objeto y se omitió.")
                self.validation["invalid_routes"] += 1
                continue
            route_id, points = raw.get("id_trm_cs"), raw.get("points")
            if not isinstance(route_id, int) or isinstance(route_id, bool) or route_id in seen_route_ids:
                self._warn(f"Routes[{index}] tiene un identificador inválido o repetido y se omitió.")
                self.validation["invalid_routes"] += 1
                continue
            if not isinstance(points, list) or len(points) < 2 or any(not self._valid_point(p) for p in points):
                self._warn(f"El tramo {route_id} tiene una geometría incompleta y se omitió.")
                self.validation["invalid_routes"] += 1
                continue
            seen_route_ids.add(route_id)
            color = raw.get("color")
            if not isinstance(color, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
                self._warn(f"El tramo {route_id} no tiene un color hexadecimal válido; se representa en gris.")
                color = "#64748b"
            route = {"id_trm_cs": route_id, "nombre_tramo": str(raw.get("nombre_tramo") or f"Tramo {route_id}"),
                     "color": color,
                     "points": [[float(p[0]), float(p[1])] for p in points]}
            self.routes.append(route)
            for a, b in zip(route["points"], route["points"][1:]):
                ka, kb = node_key(a), node_key(b)
                if ka == kb:
                    continue
                self.coordinates.setdefault(ka, a)
                self.coordinates.setdefault(kb, b)
                length = haversine(a, b)
                self.adjacency[ka].append(Edge(kb, length, route_id))
                self.adjacency[kb].append(Edge(ka, length, route_id))
            self.validation["valid_routes"] += 1

        for kind in ("Load", "Dump"):
            target = self.loads if kind == "Load" else self.dumps
            seen_ids: set[int] = set()
            for index, raw in enumerate(data[kind]):
                if (not isinstance(raw, dict) or not isinstance(raw.get("id"), int) or isinstance(raw.get("id"), bool)
                        or raw["id"] in seen_ids or not self._valid_point(raw.get("coor"))):
                    self._warn(f"{kind}[{index}] tiene datos incompletos o un identificador duplicado y se omitió.")
                    continue
                seen_ids.add(raw["id"])
                radio = raw.get("radio")
                if radio is not None and (isinstance(radio, bool) or not isinstance(radio, (int, float)) or not math.isfinite(radio) or radio < 0):
                    self._warn(f"La ubicación {raw['id']} tiene un radio inválido; se ignora ese límite.")
                    radio = None
                target.append({"id": raw["id"], "name": str(raw.get("name") or f"Ubicación {raw['id']}"),
                               "coor": [float(raw["coor"][0]), float(raw["coor"][1])],
                               "radio": radio})
        if len(self.loads) < 1 or len(self.dumps) < 1:
            raise NetworkDataError("Se necesita al menos una ubicación de carga y otra de descarga válida.")

    def _warn(self, message: str) -> None:
        self.validation["warnings"].append(message)

    def _build_components(self) -> None:
        component = 0
        for start in self.adjacency:
            if start in self.components:
                continue
            stack = [start]
            self.components[start] = component
            while stack:
                point = stack.pop()
                for edge in self.adjacency[point]:
                    if edge.target not in self.components:
                        self.components[edge.target] = component
                        stack.append(edge.target)
            component += 1
        self.component_count = component

    def _snap_locations(self, locations: list[dict[str, Any]], label: str) -> dict[int, tuple[float, float]]:
        snapped: dict[int, tuple[float, float]] = {}
        nodes = list(self.coordinates)
        for location in locations:
            node = min(nodes, key=lambda n: haversine(location["coor"], self.coordinates[n]))
            gap = haversine(location["coor"], self.coordinates[node])
            radius = location.get("radio")
            if radius is not None and gap > radius:
                self._warn(f"La {label} {location['id']} queda a {gap:.1f} m de la red, fuera de su radio de {radius} m.")
            snapped[location["id"]] = node
        return snapped

    def shortest_path(self, start: tuple[float, float], end: tuple[float, float]) -> dict[str, Any] | None:
        if self.components.get(start) != self.components.get(end):
            return None
        # The route-ID sequence is a secondary sort key when distances tie.
        first = (0.0, tuple(), start)
        heap: list[tuple[float, tuple[int, ...], tuple[float, float]]] = [first]
        best: dict[tuple[float, float], tuple[float, tuple[int, ...]]] = {start: (0.0, tuple())}
        previous: dict[tuple[float, float], tuple[tuple[float, float], int]] = {}
        while heap:
            distance, sequence, node = heapq.heappop(heap)
            current = best.get(node)
            if current is None or distance > current[0] + 1e-7 or (abs(distance - current[0]) <= 1e-7 and sequence != current[1]):
                continue
            if node == end:
                break
            for edge in self.adjacency[node]:
                candidate_distance = distance + edge.distance
                # Compare the complete edge ID sequence; every pair of consecutive
                # polyline points is a traversable edge with its own route ID.
                candidate_sequence = sequence + (edge.route_id,)
                old = best.get(edge.target)
                better = old is None or candidate_distance < old[0] - 1e-7
                tied_better = old is not None and abs(candidate_distance - old[0]) <= 1e-7 and candidate_sequence < old[1]
                if better or tied_better:
                    best[edge.target] = (candidate_distance, candidate_sequence)
                    previous[edge.target] = (node, edge.route_id)
                    heapq.heappush(heap, (candidate_distance, candidate_sequence, edge.target))
        if end not in best:
            return None
        path_nodes = [end]
        edge_ids = []
        cursor = end
        while cursor != start:
            parent, route_id = previous[cursor]
            edge_ids.append(route_id)
            path_nodes.append(parent)
            cursor = parent
        path_nodes.reverse()
        edge_ids.reverse()
        return {"points": [self.coordinates[n] for n in path_nodes], "distance_m": best[end][0], "route_ids": edge_ids}

    def reachable_pairs(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        dumps_by_component: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for location in self.dumps:
            node = self.dump_nodes[location["id"]]
            dumps_by_component[self.components[node]].append(location)
        return [(load, dump) for load in self.loads
                for dump in dumps_by_component.get(self.components[self.load_nodes[load["id"]]], [])]

    def public_network(self) -> dict[str, Any]:
        return {"routes": self.routes, "loads": self.loads, "dumps": self.dumps,
                "summary": {"route_count": len(self.routes), "load_count": len(self.loads),
                            "dump_count": len(self.dumps), "component_count": self.component_count,
                            "warnings": self.validation["warnings"]}}

