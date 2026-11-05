from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .network import NetworkDataError, RoadNetwork
from .simulation import SimulationManager

ROOT = Path(__file__).resolve().parents[2]
DATA_FILE = Path(os.getenv("DATA_FILE", str(ROOT / "data-prueba.json")))


def load_services() -> tuple[RoadNetwork, SimulationManager]:
    network = RoadNetwork.from_file(DATA_FILE)
    minimum = float(os.getenv("MIN_SPEED_KMH", "30"))
    maximum = float(os.getenv("MAX_SPEED_KMH", "60"))
    if minimum <= 0 or maximum < minimum:
        raise NetworkDataError("MIN_SPEED_KMH debe ser positivo y MAX_SPEED_KMH debe ser mayor o igual.")
    manager = SimulationManager(network, seed=int(os.getenv("SIMULATION_SEED", "3811")),
                                min_speed=minimum, max_speed=maximum,
                                interval_seconds=float(os.getenv("SIMULATION_INTERVAL_SECONDS", "2")))
    return network, manager


network, simulations = load_services()
app = FastAPI(title="IDS26-C8Q1 Road Fleet", version="1.0.0",
              description="API para red vial, simulación reproducible de cinco camiones e indicadores de velocidad.")
origins = [v.strip() for v in os.getenv("CORS_ORIGINS", "*").split(",") if v.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials="*" not in origins,
                   allow_methods=["*"], allow_headers=["*"])


class StartRequest(BaseModel):
    seed: int | None = Field(default=None, description="Semilla opcional; por defecto SIMULATION_SEED (3811).")


@app.exception_handler(NetworkDataError)
async def data_error_handler(_, exc: NetworkDataError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=500, content={"error": "invalid_network_data", "message": str(exc)})


@app.get("/api/health", tags=["Sistema"])
def health() -> dict[str, Any]:
    return {"status": "ok", "exam_code": "IDS26-C8Q1", "data_file": DATA_FILE.name,
            "network": network.public_network()["summary"]}


@app.get("/api/network", tags=["Red"])
def get_network() -> dict[str, Any]:
    return network.public_network()


@app.get("/api/routes", tags=["Red"])
def get_routes() -> list[dict[str, Any]]:
    return network.routes


@app.get("/api/locations", tags=["Red"])
def get_locations() -> dict[str, list[dict[str, Any]]]:
    return {"loads": network.loads, "dumps": network.dumps}


@app.post("/api/simulations", status_code=201, tags=["Simulación"])
async def start_simulation(body: StartRequest | None = None) -> dict[str, Any]:
    try:
        return await simulations.start(body.seed if body else None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "unreachable_network", "message": str(exc)}) from exc


@app.get("/api/simulations/current", tags=["Simulación"])
def current_simulation() -> dict[str, Any]:
    return simulations.current()


@app.get("/api/simulations/report", tags=["Reporte"])
def simulation_report() -> dict[str, Any]:
    return simulations.report()


@app.get("/api/openapi.json", include_in_schema=False)
def openapi_json() -> dict[str, Any]:
    return app.openapi()

