"""FastAPI app: serves the static CesiumJS frontend and the precomputed JSON.

The pipeline (pipeline/run_pipeline.py) writes data/orbits/orbits.json and
data/events/events.json. This app reads them once at startup (cached in
memory; /api/refresh re-reads for dev convenience) and exposes:

    GET /api/config      -> pipeline config + fetch timestamp
    GET /api/satellites   -> [{sat_id, name}, ...]
    GET /api/orbits       -> full orbits payload (optionally ?ids=a,b)
    GET /api/events       -> events filtered by ?threshold_km= &min_rel_vel=
    GET /                 -> static frontend (index.html, app.js, style.css)
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (  # noqa: E402
    CESIUM_ION_TOKEN,
    EVENTS_FILE,
    FETCH_DELAY_S,
    FRONTEND_DIR,
    MIN_CLOSING_VEL_KM_S,
    N_MAX,
    ORBITS_FILE,
    OUTPUT_STEP_MIN,
    SAT_GROUP,
    SAT_GROUPS,
    STEP_MIN,
    THRESHOLD_KM,
    TIME_WINDOW_HRS,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

app = FastAPI(title="Satellite Collision Risk Tracker")

# In-memory cache of the precomputed data.
_cache: dict = {"orbits": None, "events": None}


def _load() -> None:
    """(Re)load the precomputed JSON files from disk into the cache."""
    if ORBITS_FILE.exists():
        _cache["orbits"] = json.loads(ORBITS_FILE.read_text(encoding="utf-8"))
        log.info("Loaded %s (%d sats)", ORBITS_FILE.name, len(_cache["orbits"].get("orbits", [])))
    else:
        _cache["orbits"] = None
        log.warning("No orbits file at %s — run the pipeline first.", ORBITS_FILE)
    if EVENTS_FILE.exists():
        _cache["events"] = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
        log.info("Loaded %s (%d events)", EVENTS_FILE.name, len(_cache["events"].get("events", [])))
    else:
        _cache["events"] = None


@app.on_event("startup")
def _startup() -> None:
    _load()


@app.get("/api/refresh")
def refresh() -> dict:
    """Re-read the JSON files from disk (dev convenience)."""
    _load()
    return {"ok": True}


@app.get("/api/config")
def get_config() -> dict:
    return {
        "sat_group": SAT_GROUP,
        "sat_groups": SAT_GROUPS,
        "n_max": N_MAX,
        "time_window_hrs": TIME_WINDOW_HRS,
        "step_min": STEP_MIN,
        "output_step_min": OUTPUT_STEP_MIN,
        "threshold_km": THRESHOLD_KM,
        "min_closing_vel_km_s": MIN_CLOSING_VEL_KM_S,
        "fetch_delay_s": FETCH_DELAY_S,
        "fetch_time": (_cache["orbits"] or {}).get("fetch_time"),
        "cesium_ion_token": CESIUM_ION_TOKEN,
    }


@app.get("/api/satellites")
def get_satellites() -> list[dict]:
    orbits = (_cache["orbits"] or {}).get("orbits", [])
    return [{"sat_id": o["sat_id"], "name": o["name"]} for o in orbits]


@app.get("/api/orbits")
def get_orbits(ids: Optional[str] = Query(None)) -> dict:
    payload = _cache["orbits"]
    if payload is None:
        return JSONResponse({"error": "no orbits available — run the pipeline"}, status_code=503)
    if ids:
        wanted = set(ids.split(","))
        orbits = [o for o in payload["orbits"] if o["sat_id"] in wanted]
        return {**payload, "orbits": orbits}
    return payload


@app.get("/api/events")
def get_events(
    threshold_km: Optional[float] = Query(None, description="max distance (km)"),
    min_rel_vel: Optional[float] = Query(None, description="min relative closing speed (km/s)"),
) -> dict:
    payload = _cache["events"]
    if payload is None:
        return JSONResponse({"error": "no events available — run the pipeline"}, status_code=503)
    events = payload.get("events", [])
    thr = threshold_km if threshold_km is not None else float("inf")
    mrv = min_rel_vel if min_rel_vel is not None else 0.0
    filtered = [
        e for e in events
        if e["distance_km"] <= thr and (e.get("rel_vel_km_s") or 0.0) >= mrv
    ]
    return {**payload, "events": filtered, "n_events": len(filtered)}


# --- Static frontend -----------------------------------------------------
# Serve frontend assets (app.js, style.css, favicon) at / and root -> index.html.
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/app.js")
def app_js() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "app.js", media_type="application/javascript")


@app.get("/style.css")
def style_css() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "style.css", media_type="text/css")