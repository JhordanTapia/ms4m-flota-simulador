from __future__ import annotations

import asyncio
import bisect
import os
import random
import time
from datetime import datetime, timezone
from typing import Any

from .network import RoadNetwork


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SimulationManager:
    def __init__(self, network: RoadNetwork, *, seed: int = 3811, min_speed: float = 30,
                 max_speed: float = 60, interval_seconds: float = 2,
                 high_speed_warning_percent: float = 25, few_samples_threshold: int = 5,
                 difference_threshold_kmh: float = 2):
        self.network = network
        self.seed = seed
        self.min_speed = min_speed
        self.max_speed = max_speed
        self.interval_seconds = interval_seconds
        self.high_speed_warning_percent = high_speed_warning_percent
        self.few_samples_threshold = few_samples_threshold
        self.difference_threshold_kmh = difference_threshold_kmh
        self.simulation_id = 0
        self.simulation: dict[str, Any] | None = None
        self._rng = random.Random(seed)
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self, seed: int | None = None) -> dict[str, Any]:
        async with self._lock:
            chosen_seed = self.seed if seed is None else seed
            rng = random.Random(chosen_seed)
            pairs = self.network.reachable_pairs()
            if not pairs:
                raise ValueError("No hay ningún par de carga y descarga conectado por la red.")
            total_pairs = len(self.network.loads) * len(self.network.dumps)
            route_selection = {"policy": "selección reproducible entre pares alcanzables",
                               "reachable_pairs": len(pairs),
                               "unreachable_pairs_skipped": total_pairs - len(pairs),
                               "pairs_reused_to_fill_five": len(pairs) < 5}
            rng.shuffle(pairs)
            if len(pairs) < 5:
                pairs = [pairs[i % len(pairs)] for i in range(5)]
            else:
                pairs = pairs[:5]
            self._rng = rng
            self.simulation_id += 1
            trucks = []
            for index, (origin, destination) in enumerate(pairs, start=1):
                start_node = self.network.load_nodes[origin["id"]]
                end_node = self.network.dump_nodes[destination["id"]]
                path = self.network.shortest_path(start_node, end_node)
                if path is None:
                    continue
                points = path["points"]
                cumulative = [0.0]
                for a, b in zip(points, points[1:]):
                    from .network import haversine
                    cumulative.append(cumulative[-1] + haversine(a, b))
                speed = self._rng.uniform(self.min_speed, self.max_speed)
                trucks.append({
                    "id": f"C8Q1-{index:03d}", "origin": origin, "destination": destination,
                    "route": points, "route_ids": path["route_ids"], "distance_m": path["distance_m"],
                    "cumulative_m": cumulative, "progress_m": 0.0,
                    "position": points[0], "speed_kmh": round(speed, 2), "status": "en_ruta",
                    "updated_at": utc_now(), "samples": [],
                })
            if len(trucks) != 5:
                raise ValueError("No se pudieron generar cinco recorridos válidos; revisa la conectividad de la red.")
            now = time.monotonic()
            for truck in trucks:
                truck["samples"].append(self._sample(truck))
            self.simulation = {"id": self.simulation_id, "seed": chosen_seed, "status": "en_curso",
                               "started_at": utc_now(), "updated_at": utc_now(), "interval_seconds": self.interval_seconds,
                               "speed_limits_kmh": {"min": self.min_speed, "max": self.max_speed},
                               "route_selection": route_selection, "trucks": trucks, "_last_tick": now}
            if self._task and not self._task.done():
                self._task.cancel()
            self._task = asyncio.create_task(self._run_loop(self.simulation_id))
            return self.current()

    def _sample(self, truck: dict[str, Any]) -> dict[str, Any]:
        return {"timestamp": utc_now(), "speed_kmh": truck["speed_kmh"],
                "position": list(truck["position"]), "status": truck["status"]}

    async def _run_loop(self, simulation_id: int) -> None:
        while self.simulation and self.simulation["id"] == simulation_id:
            await asyncio.sleep(self.interval_seconds)
            async with self._lock:
                if not self.simulation or self.simulation["id"] != simulation_id:
                    return
                now = time.monotonic()
                elapsed = max(0, now - self.simulation["_last_tick"])
                self.simulation["_last_tick"] = now
                for truck in self.simulation["trucks"]:
                    if truck["status"] == "finalizado":
                        continue
                    # Apply the speed that was active during the elapsed interval;
                    # choose the next speed only after the truck has advanced.
                    truck["progress_m"] = min(truck["distance_m"], truck["progress_m"] + truck["speed_kmh"] / 3.6 * elapsed)
                    truck["position"] = self._position_at(truck, truck["progress_m"])
                    if truck["progress_m"] >= truck["distance_m"]:
                        truck["status"] = "finalizado"
                        truck["speed_kmh"] = 0.0
                    else:
                        truck["speed_kmh"] = round(self._rng.uniform(self.min_speed, self.max_speed), 2)
                    truck["updated_at"] = utc_now()
                    # Speeds are sampled only while moving, so the stop does not distort the speed report.
                    if truck["status"] == "en_ruta":
                        truck["samples"].append(self._sample(truck))
                finished = all(t["status"] == "finalizado" for t in self.simulation["trucks"])
                if finished:
                    self.simulation["status"] = "finalizada"
                self.simulation["updated_at"] = utc_now()
                if finished:
                    return

    @staticmethod
    def _position_at(truck: dict[str, Any], distance: float) -> list[float]:
        cumulative = truck["cumulative_m"]
        points = truck["route"]
        if distance >= cumulative[-1]:
            return list(points[-1])
        segment = max(0, bisect.bisect_right(cumulative, distance) - 1)
        start, end = cumulative[segment], cumulative[segment + 1]
        ratio = 0 if end == start else (distance - start) / (end - start)
        return [points[segment][i] + (points[segment + 1][i] - points[segment][i]) * ratio for i in (0, 1)]

    def current(self, *, include_routes: bool = True) -> dict[str, Any]:
        if self.simulation is None:
            return {"status": "sin_iniciar", "simulation": None}
        sim = self.simulation
        trucks = []
        for t in sim["trucks"]:
            item = {"id": t["id"], "origin": t["origin"], "destination": t["destination"],
                    "position": list(t["position"]), "speed_kmh": t["speed_kmh"],
                    "status": t["status"], "updated_at": t["updated_at"],
                    "distance_m": round(t["distance_m"], 2), "progress_m": round(t["progress_m"], 2),
                    "route_ids": t["route_ids"]}
            if include_routes:
                item["route"] = t["route"]
            trucks.append(item)
        return {"id": sim["id"], "seed": sim["seed"], "status": sim["status"],
                "started_at": sim["started_at"], "updated_at": sim["updated_at"],
                "interval_seconds": sim["interval_seconds"], "speed_limits_kmh": sim["speed_limits_kmh"],
                "route_selection": sim["route_selection"], "trucks": trucks}

    def report(self) -> dict[str, Any]:
        if self.simulation is None:
            return {"simulation_id": None, "trucks": [], "explanation": "Inicia una simulación para generar el reporte."}
        rows = []
        averages = []
        for t in self.simulation["trucks"]:
            values = [s["speed_kmh"] for s in t["samples"]]
            avg = round(sum(values) / len(values), 2) if values else None
            above = sum(v > 55 for v in values)
            rows.append({"truck_id": t["id"], "samples": len(values),
                         "min_speed_kmh": round(min(values), 2) if values else None,
                         "max_speed_kmh": round(max(values), 2) if values else None,
                         "average_speed_kmh": avg, "above_55_kmh": above,
                         "above_55_percent": round(above * 100 / len(values), 1) if values else None,
                         "status": t["status"]})
            if avg is not None:
                averages.append((t["id"], avg))
        explanation = self._explain(rows, averages)
        return {"simulation_id": self.simulation["id"], "seed": self.simulation["seed"],
                "sampling_interval_seconds": self.interval_seconds, "metric": "Media aritmética de muestras de velocidad en movimiento.",
                "heuristic_thresholds": {"few_samples_below": self.few_samples_threshold,
                                         "above_55_warning_percent": self.high_speed_warning_percent,
                                         "fleet_difference_kmh": self.difference_threshold_kmh},
                "trucks": rows, "explanation": explanation}

    def _explain(self, rows: list[dict[str, Any]], averages: list[tuple[str, float]]) -> str:
        if not averages:
            return "Todavía no hay muestras de velocidad para comparar."
        fleet_avg = sum(x[1] for x in averages) / len(averages)
        fastest = max(averages, key=lambda x: (x[1], x[0]))
        slowest = min(averages, key=lambda x: (x[1], x[0]))
        sentences = [f"El promedio de la flota es {fleet_avg:.2f} km/h. {fastest[0]} registra el mayor promedio ({fastest[1]:.2f} km/h) y {slowest[0]} el menor ({slowest[1]:.2f} km/h)."]
        few = [r["truck_id"] for r in rows if r["samples"] < self.few_samples_threshold]
        if few:
            sentences.append(f"Hay pocas muestras (menos de {self.few_samples_threshold}) para: {', '.join(few)}.")
        high = [r for r in rows if r["samples"] > 0
                and r["above_55_kmh"] * 100 > self.high_speed_warning_percent * r["samples"]]
        if high:
            sentences.append(f"Más del {self.high_speed_warning_percent:g} % de las muestras supera 55 km/h en: " + ", ".join(f"{r['truck_id']} ({r['above_55_percent']:.1f} %)" for r in high) + ".")
        differences = [(truck_id, average - fleet_avg) for truck_id, average in averages
                       if abs(average - fleet_avg) >= self.difference_threshold_kmh]
        if differences:
            comparisons = [f"{truck_id} está {abs(delta):.2f} km/h {'por encima' if delta > 0 else 'por debajo'}" for truck_id, delta in differences]
            sentences.append(f"Respecto al promedio de la flota, {', '.join(comparisons)}.")
        else:
            sentences.append(f"Todos los promedios individuales están a menos de {self.difference_threshold_kmh:g} km/h del promedio de la flota.")
        return " ".join(sentences)

