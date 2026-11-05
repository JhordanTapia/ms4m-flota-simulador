from __future__ import annotations

import os
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .network import NetworkDataError, RoadNetwork
from .simulation import SimulationManager

ROOT = Path(__file__).resolve().parents[2]
DATA_FILE = Path(os.getenv("DATA_FILE", str(ROOT / "data-prueba.json")))


def load_services() -> tuple[RoadNetwork, SimulationManager]:
    try:
        minimum = float(os.getenv("MIN_SPEED_KMH", "30"))
        maximum = float(os.getenv("MAX_SPEED_KMH", "60"))
        interval = float(os.getenv("SIMULATION_INTERVAL_SECONDS", "2"))
        seed = int(os.getenv("SIMULATION_SEED", "3811"))
        high_speed_percent = float(os.getenv("HIGH_SPEED_WARNING_PERCENT", "25"))
        few_samples = int(os.getenv("FEW_SAMPLES_THRESHOLD", "5"))
        difference_threshold = float(os.getenv("REPORT_DIFFERENCE_THRESHOLD_KMH", "2"))
    except ValueError as exc:
        raise NetworkDataError("La configuración de simulación contiene un valor inválido.") from exc
    if not math.isfinite(minimum) or not math.isfinite(maximum) or minimum <= 0 or maximum < minimum:
        raise NetworkDataError("MIN_SPEED_KMH debe ser positivo y MAX_SPEED_KMH debe ser mayor o igual.")
    if not math.isfinite(interval) or interval <= 0:
        raise NetworkDataError("SIMULATION_INTERVAL_SECONDS debe ser mayor que cero.")
    if (not math.isfinite(high_speed_percent) or not 0 <= high_speed_percent <= 100
            or few_samples < 1 or not math.isfinite(difference_threshold) or difference_threshold <= 0):
        raise NetworkDataError("Los umbrales del reporte deben ser porcentajes entre 0 y 100, muestras positivas y diferencias mayores que cero.")
    network = RoadNetwork.from_file(DATA_FILE)
    manager = SimulationManager(network, seed=seed, min_speed=minimum, max_speed=maximum,
                                interval_seconds=interval,
                                high_speed_warning_percent=high_speed_percent,
                                few_samples_threshold=few_samples,
                                difference_threshold_kmh=difference_threshold)
    return network, manager


app = FastAPI(title="IDS26-C8Q1 Road Fleet", version="1.0.0",
              description="API para red vial, simulación reproducible de cinco camiones e indicadores de velocidad.",
              docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json")
network: RoadNetwork | None = None
simulations: SimulationManager | None = None
startup_error: str | None = None
try:
    network, simulations = load_services()
except (NetworkDataError, OSError, ValueError) as exc:
    startup_error = str(exc)

origins = [v.strip() for v in os.getenv("CORS_ORIGINS", "*").split(",") if v.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials="*" not in origins,
                   allow_methods=["*"], allow_headers=["*"])


class StartRequest(BaseModel):
    seed: int | None = Field(default=None, description="Semilla opcional; por defecto SIMULATION_SEED (3811).")


@app.exception_handler(RequestValidationError)
async def request_validation_handler(_, __: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": {
        "error": "validation_error", "message": "El cuerpo de la solicitud no es válido."}})


def require_services() -> tuple[RoadNetwork, SimulationManager]:
    if network is None or simulations is None:
        raise HTTPException(status_code=503, detail={"error": "invalid_network_data",
                            "message": startup_error or "La red vial no está disponible."})
    return network, simulations


@app.get("/api/health", tags=["Sistema"])
def health() -> dict[str, Any]:
    current_network, _ = require_services()
    return {"status": "ok", "exam_code": "IDS26-C8Q1", "data_file": DATA_FILE.name,
            "network": current_network.public_network()["summary"]}


@app.get("/api/network", tags=["Red"])
def get_network() -> dict[str, Any]:
    current_network, _ = require_services()
    return current_network.public_network()


@app.get("/api/routes", tags=["Red"])
def get_routes() -> list[dict[str, Any]]:
    current_network, _ = require_services()
    return current_network.routes


@app.get("/api/locations", tags=["Red"])
def get_locations() -> dict[str, list[dict[str, Any]]]:
    current_network, _ = require_services()
    return {"loads": current_network.loads, "dumps": current_network.dumps}


@app.post("/api/simulations", status_code=201, tags=["Simulación"])
def start_simulation(body: StartRequest | None = None) -> dict[str, Any]:
    _, manager = require_services()
    try:
        return manager.start(body.seed if body else None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "unreachable_network", "message": str(exc)}) from exc


@app.get("/api/simulations/current", tags=["Simulación"])
def current_simulation(seed: int | None = None, started_at: datetime | None = None) -> dict[str, Any]:
    _, manager = require_services()
    try:
        return manager.current(seed, started_at, include_routes=False)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_simulation_reference", "message": str(exc)}) from exc


@app.get("/api/simulations/report", tags=["Reporte"])
def simulation_report(seed: int | None = None, started_at: datetime | None = None) -> dict[str, Any]:
    _, manager = require_services()
    try:
        return manager.report(seed, started_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_simulation_reference", "message": str(exc)}) from exc


@app.get("/api/simulations/snapshot", tags=["Simulación", "Reporte"])
def simulation_snapshot(seed: int | None = None, started_at: datetime | None = None) -> dict[str, Any]:
    _, manager = require_services()
    try:
        return manager.snapshot(seed, started_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_simulation_reference", "message": str(exc)}) from exc
