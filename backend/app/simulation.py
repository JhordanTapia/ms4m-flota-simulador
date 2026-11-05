from __future__ import annotations

import bisect
import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from .network import RoadNetwork, haversine


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SimulationManager:
    """Builds simulation snapshots from a seed and start time without process state.

    Vercel may route consecutive requests to different function instances. Keeping
    only the immutable road network in memory and reconstructing each run from its
    reference makes current positions and reports consistent across instances.
    """

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

    @staticmethod
    def _parse_started_at(value: datetime | str) -> datetime:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if parsed.tzinfo is None:
            raise ValueError("started_at debe incluir una zona horaria.")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")

    def _initialize(self, seed: int, started_at: datetime) -> tuple[dict[str, Any], random.Random]:
        rng = random.Random(seed)
        pairs = self.network.reachable_pairs()
        if not pairs:
            raise ValueError("No hay ningún par de carga y descarga conectado por la red.")

        total_pairs = len(self.network.loads) * len(self.network.dumps)
        route_selection = {
            "policy": "selección reproducible entre pares alcanzables",
            "reachable_pairs": len(pairs),
            "unreachable_pairs_skipped": total_pairs - len(pairs),
            "pairs_reused_to_fill_five": len(pairs) < 5,
        }
        rng.shuffle(pairs)
        if len(pairs) < 5:
            pairs = [pairs[i % len(pairs)] for i in range(5)]
        else:
            pairs = pairs[:5]

        trucks: list[dict[str, Any]] = []
        for index, (origin, destination) in enumerate(pairs, start=1):
            start_node = self.network.load_nodes[origin["id"]]
            end_node = self.network.dump_nodes[destination["id"]]
            path = self.network.shortest_path(start_node, end_node)
            if path is None:
                continue
            points = path["points"]
            cumulative = [0.0]
            for a, b in zip(points, points[1:]):
                cumulative.append(cumulative[-1] + haversine(a, b))
            speed = round(rng.uniform(self.min_speed, self.max_speed), 2)
            trucks.append({
                "id": f"C8Q1-{index:03d}", "origin": origin, "destination": destination,
                "route": points, "route_ids": path["route_ids"], "distance_m": path["distance_m"],
                "cumulative_m": cumulative, "progress_m": 0.0, "position": list(points[0]),
                "speed_kmh": speed, "status": "en_ruta", "updated_at": self._iso(started_at),
                "samples": [{"timestamp": self._iso(started_at), "speed_kmh": speed}],
            })

        if len(trucks) != 5:
            raise ValueError("No se pudieron generar cinco recorridos válidos; revisa la conectividad de la red.")

        started_text = self._iso(started_at)
        run_id = hashlib.sha256(f"{seed}|{started_text}".encode("utf-8")).hexdigest()[:16]
        run = {
            "id": run_id, "seed": seed, "started_at": started_text,
            "updated_at": started_text, "status": "en_curso", "interval_seconds": self.interval_seconds,
            "speed_limits_kmh": {"min": self.min_speed, "max": self.max_speed},
            "route_selection": route_selection, "trucks": trucks,
        }
        return run, rng

    def _build(self, seed: int, started_at: datetime, now: datetime | None = None) -> dict[str, Any]:
        started_at = self._parse_started_at(started_at)
        now = (now or utc_now()).astimezone(timezone.utc)
        elapsed_us = max(0, int((now - started_at).total_seconds() * 1_000_000))
        interval_us = max(1, int(round(self.interval_seconds * 1_000_000)))
        completed_ticks = elapsed_us // interval_us
        remainder_seconds = (elapsed_us - completed_ticks * interval_us) / 1_000_000
        run, rng = self._initialize(seed, started_at)
        trucks = run["trucks"]

        for tick in range(1, completed_ticks + 1):
            tick_at = started_at + timedelta(microseconds=tick * interval_us)
            for truck in trucks:
                if truck["status"] == "finalizado":
                    continue
                truck["progress_m"] = min(
                    truck["distance_m"],
                    truck["progress_m"] + truck["speed_kmh"] / 3.6 * self.interval_seconds,
                )
                truck["position"] = self._position_at(truck, truck["progress_m"])
                if truck["progress_m"] >= truck["distance_m"]:
                    truck["status"] = "finalizado"
                    truck["speed_kmh"] = 0.0
                else:
                    truck["speed_kmh"] = round(rng.uniform(self.min_speed, self.max_speed), 2)
                    truck["samples"].append({
                        "timestamp": self._iso(tick_at), "speed_kmh": truck["speed_kmh"],
                    })
                truck["updated_at"] = self._iso(tick_at)
            run["updated_at"] = self._iso(tick_at)
            if all(truck["status"] == "finalizado" for truck in trucks):
                break

        active_trucks = [truck for truck in trucks if truck["status"] != "finalizado"]
        if remainder_seconds > 0 and active_trucks:
            for truck in active_trucks:
                truck["progress_m"] = min(
                    truck["distance_m"],
                    truck["progress_m"] + truck["speed_kmh"] / 3.6 * remainder_seconds,
                )
                truck["position"] = self._position_at(truck, truck["progress_m"])
                if truck["progress_m"] >= truck["distance_m"]:
                    truck["status"] = "finalizado"
                    truck["speed_kmh"] = 0.0
                truck["updated_at"] = self._iso(now)
            run["updated_at"] = self._iso(now)

        if all(truck["status"] == "finalizado" for truck in trucks):
            run["status"] = "finalizada"
        return run

    def start(self, seed: int | None = None) -> dict[str, Any]:
        chosen_seed = self.seed if seed is None else seed
        started_at = datetime.fromisoformat(self._iso(utc_now()))
        return self._public_current(self._build(chosen_seed, started_at, started_at), include_routes=True)

    def current(self, seed: int | None = None, started_at: datetime | str | None = None,
                *, include_routes: bool = True) -> dict[str, Any]:
        if seed is None and started_at is None:
            return {"status": "sin_iniciar", "simulation": None}
        if seed is None or started_at is None:
            raise ValueError("seed y started_at deben enviarse juntos.")
        return self._public_current(self._build(seed, self._parse_started_at(started_at)), include_routes=include_routes)

    @staticmethod
    def _public_current(run: dict[str, Any], *, include_routes: bool) -> dict[str, Any]:
        trucks = []
        for truck in run["trucks"]:
            item = {
                "id": truck["id"], "origin": truck["origin"], "destination": truck["destination"],
                "position": list(truck["position"]), "speed_kmh": truck["speed_kmh"],
                "status": truck["status"], "updated_at": truck["updated_at"],
                "distance_m": round(truck["distance_m"], 2),
                "progress_m": round(truck["progress_m"], 2), "route_ids": truck["route_ids"],
            }
            if include_routes:
                item["route"] = truck["route"]
            trucks.append(item)
        return {
            "id": run["id"], "simulation_id": run["id"], "seed": run["seed"],
            "started_at": run["started_at"], "status": run["status"],
            "updated_at": run["updated_at"], "interval_seconds": run["interval_seconds"],
            "speed_limits_kmh": run["speed_limits_kmh"], "route_selection": run["route_selection"],
            "trucks": trucks,
        }

    def report(self, seed: int | None = None, started_at: datetime | str | None = None) -> dict[str, Any]:
        if seed is None and started_at is None:
            return {"simulation_id": None, "trucks": [],
                    "explanation": "Inicia una simulación para generar el reporte."}
        if seed is None or started_at is None:
            raise ValueError("seed y started_at deben enviarse juntos.")
        run = self._build(seed, self._parse_started_at(started_at))
        return self._report_run(run)

    def snapshot(self, seed: int | None = None, started_at: datetime | str | None = None) -> dict[str, Any]:
        if seed is None and started_at is None:
            return {"simulation": {"status": "sin_iniciar", "simulation": None}, "report": self.report()}
        if seed is None or started_at is None:
            raise ValueError("seed y started_at deben enviarse juntos.")
        run = self._build(seed, self._parse_started_at(started_at))
        return {
            "simulation": self._public_current(run, include_routes=False),
            "report": self._report_run(run),
        }

    def _report_run(self, run: dict[str, Any]) -> dict[str, Any]:
        rows = []
        averages = []
        for truck in run["trucks"]:
            values = [sample["speed_kmh"] for sample in truck["samples"]]
            average = round(sum(values) / len(values), 2) if values else None
            above = sum(value > 55 for value in values)
            rows.append({
                "truck_id": truck["id"], "samples": len(values),
                "min_speed_kmh": round(min(values), 2) if values else None,
                "max_speed_kmh": round(max(values), 2) if values else None,
                "average_speed_kmh": average, "above_55_kmh": above,
                "above_55_percent": round(above * 100 / len(values), 1) if values else None,
                "status": truck["status"],
            })
            if average is not None:
                averages.append((truck["id"], average))
        return {
            "simulation_id": run["id"], "seed": run["seed"],
            "sampling_interval_seconds": self.interval_seconds,
            "metric": "Media aritmética de muestras de velocidad en movimiento.",
            "heuristic_thresholds": {
                "few_samples_below": self.few_samples_threshold,
                "above_55_warning_percent": self.high_speed_warning_percent,
                "fleet_difference_kmh": self.difference_threshold_kmh,
            },
            "trucks": rows, "explanation": self._explain(rows, averages),
        }

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

    def _explain(self, rows: list[dict[str, Any]], averages: list[tuple[str, float]]) -> str:
        if not averages:
            return "Todavía no hay muestras de velocidad para comparar."
        fleet_average = sum(value for _, value in averages) / len(averages)
        fastest = max(averages, key=lambda item: (item[1], item[0]))
        slowest = min(averages, key=lambda item: (item[1], item[0]))
        sentences = [
            f"El promedio de la flota es {fleet_average:.2f} km/h. "
            f"{fastest[0]} registra el mayor promedio ({fastest[1]:.2f} km/h) y "
            f"{slowest[0]} el menor ({slowest[1]:.2f} km/h)."
        ]
        few = [row["truck_id"] for row in rows if row["samples"] < self.few_samples_threshold]
        if few:
            sentences.append(f"Hay pocas muestras (menos de {self.few_samples_threshold}) para: {', '.join(few)}.")
        high = [row for row in rows if row["samples"] > 0
                and row["above_55_kmh"] * 100 > self.high_speed_warning_percent * row["samples"]]
        if high:
            sentences.append(
                f"Más del {self.high_speed_warning_percent:g} % de las muestras supera 55 km/h en: "
                + ", ".join(f"{row['truck_id']} ({row['above_55_percent']:.1f} %)" for row in high) + "."
            )
        differences = [(truck_id, average - fleet_average) for truck_id, average in averages
                       if abs(average - fleet_average) >= self.difference_threshold_kmh]
        if differences:
            comparisons = [
                f"{truck_id} está {abs(delta):.2f} km/h {'por encima' if delta > 0 else 'por debajo'}"
                for truck_id, delta in differences
            ]
            sentences.append(f"Respecto al promedio de la flota, {', '.join(comparisons)}.")
        else:
            sentences.append(
                f"Todos los promedios individuales están a menos de "
                f"{self.difference_threshold_kmh:g} km/h del promedio de la flota."
            )
        return " ".join(sentences)
