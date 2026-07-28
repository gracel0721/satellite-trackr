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
FRONTEND_DIR = BASE_DIR / "frontend"

# --- Data source ---------------------------------------------------------
# CelesTrak GP endpoint. GROUP selects the catalog; FORMAT=tle returns the
# classic 3-line TLE records (name / line1 / line2).
CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"
SAT_GROUP = os.environ.get("SAT_GROUP", "starlink")

# --- Propagation window --------------------------------------------------
TIME_WINDOW_HRS = int(os.environ.get("TIME_WINDOW_HRS", "24"))  # span to propagate
STEP_MIN = int(os.environ.get("STEP_MIN", "1"))                # minutes between samples
N_MAX = int(os.environ.get("N_MAX", "200"))                    # cap satellites for O(n^2)

# --- Close-approach detection -------------------------------------------
# Distance threshold in km. NOTE: genuine <10 km conjunctions are rare in a
# ~200-satellite subset over 24h, so the demo default is set higher so the UI
# surfaces visible "close approaches". The math is identical either way; this
# is purely a reporting cutoff. Lower it (e.g. 10) for realistic screening.
THRESHOLD_KM = float(os.environ.get("THRESHOLD_KM", "50.0"))
# Optional: ignore slow-flying pairs where the relative closing speed is below
# this (km/s). 0 disables the filter and flags purely on distance.
MIN_CLOSING_VEL_KM_S = float(os.environ.get("MIN_CLOSING_VEL_KM_S", "0.0"))
FLAG_REL_VEL = True  # include relative velocity in event output

# --- Frontend / deploy --------------------------------------------------
# Optional Cesium Ion access token for terrain/imagery. If unset the app
# falls back to free default imagery/terrain so it runs without an account.
CESIUM_ION_TOKEN = os.environ.get("CESIUM_ION_TOKEN", "")