"""Central configuration for the Satellite Collision Risk Tracker.

All pipeline knobs and the values surfaced to the frontend via /api/config
live here. Tweak these to change the satellite group, time window, and the
close-approach threshold.
"""
import os
from pathlib import Path

# --- Paths ---------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
ORBITS_DIR = DATA_DIR / "orbits"
EVENTS_DIR = DATA_DIR / "events"
ORBITS_FILE = ORBITS_DIR / "orbits.json"
EVENTS_FILE = EVENTS_DIR / "events.json"
# Static frontend lives in public/ — Vercel's FastAPI preset serves the public/
# directory at the site root by default (NOT outputDirectory, which the FastAPI
# preset does not honor for static assets). Local dev (uvicorn) also serves from
# here via api/main.py's VERCEL-guarded static routes.
FRONTEND_DIR = BASE_DIR / "public"

# --- Data source ---------------------------------------------------------
# CelesTrak GP endpoint. GROUP selects the catalog; FORMAT=tle returns the
# classic 3-line TLE records (name / line1 / line2).
CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"
SAT_GROUP = os.environ.get("SAT_GROUP", "starlink")
SAT_GROUPS = [
    g.strip()
    for g in os.environ.get("SAT_GROUPS", SAT_GROUP).split(",")
    if g.strip()
]

# --- Propagation window --------------------------------------------------
TIME_WINDOW_HRS = int(os.environ.get("TIME_WINDOW_HRS", "24"))  # span to propagate
STEP_MIN = int(os.environ.get("STEP_MIN", "1"))                # minutes between samples for analysis
# Output grid for the frontend JSON. Defaults to 5 minutes so the app stays
# responsive when processing thousands of satellites. Analysis still runs at
# full ``STEP_MIN`` resolution; only the committed visualization payload is
# decimated. Set to ``STEP_MIN`` for full-resolution output.
_output_step_raw = os.environ.get("OUTPUT_STEP_MIN", "5").strip()
OUTPUT_STEP_MIN = int(_output_step_raw) if _output_step_raw else STEP_MIN

# Optional cap on total satellites processed. Empty/unset means no cap (process
# everything fetched). A cap is still useful for quick dev runs or memory limits.
_n_max_raw = os.environ.get("N_MAX", "").strip()
N_MAX = int(_n_max_raw) if _n_max_raw else None

# --- Fetch politeness / caching ------------------------------------------
# Seconds to sleep between live CelesTrak group fetches (be polite).
FETCH_DELAY_S = float(os.environ.get("FETCH_DELAY_S", "1.0"))
# Reuse a cached TLE file if it is newer than this many hours, avoiding a
# fresh CelesTrak request. Set to 0 to always fetch live.
CACHE_TTL_HRS = float(os.environ.get("CACHE_TTL_HRS", "6.0"))

# --- Close-approach detection -------------------------------------------
# Distance threshold in km. Default 10 km is a realistic conjunction-screening
# cutoff for operational use. Raise it (e.g. 50) if you want more visual
# "close approaches" in a sparse demo dataset.
THRESHOLD_KM = float(os.environ.get("THRESHOLD_KM", "10.0"))
# Optional: ignore slow-flying pairs where the relative closing speed is below
# this (km/s). 0 disables the filter and flags purely on distance.
MIN_CLOSING_VEL_KM_S = float(os.environ.get("MIN_CLOSING_VEL_KM_S", "0.0"))
FLAG_REL_VEL = True  # include relative velocity in event output

# --- Frontend / deploy --------------------------------------------------
# Optional Cesium Ion access token for terrain/imagery. If unset the app
# falls back to free default imagery/terrain so it runs without an account.
CESIUM_ION_TOKEN = os.environ.get("CESIUM_ION_TOKEN", "")

# Public URL of the merged positions.json the Cloud Function publishes to GCS.
# When set (production deploy), the frontend fetches the data straight from
# the bucket instead of the repo's committed data files. Leave empty for local
# dev, where the frontend falls back to the /api/orbits + /api/events endpoints.
DATA_URL = os.environ.get("DATA_URL", "")