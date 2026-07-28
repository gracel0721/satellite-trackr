# Satellite Collision Risk Tracker 🛰️

A portfolio project that pulls **live satellite orbital data** from
[CelesTrak](https://celestrak.org), propagates orbits with the `sgp4`
algorithm, computes **close-approach ("collision risk") events** between
satellites using vectorized numpy, and visualizes them on an interactive
**CesiumJS 3D globe** wrapped in a FastAPI app.

Low Earth orbit is increasingly congested. Operators need tools to identify
when two objects are approaching each other dangerously close so they can plan
avoidance maneuvers. This project is a simplified, demo-able version of that
conjunction-analysis tool, built entirely on public data.

---

## Architecture

```
pipeline (CLI / GitHub Action, every 6h)
  fetch TLEs (CelesTrak) ──▶ data/raw/*.tle
  propagate with sgp4     ──▶ data/orbits/orbits.json  (ECEF xyz, meters)
  close-approach analysis ──▶ data/events/events.json
                 │
                 ▼
data/*.json ── served read-only by ── FastAPI (api/main.py)
                 │   GET /api/config  /api/satellites  /api/orbits  /api/events
                 ▼
CesiumJS frontend (frontend/index.html + app.js)
  3D globe · moving satellite dots · time-scrub (Cesium clock + timeline)
  red close-approach pairs + connecting lines · alerts panel · threshold slider
```

**Design choices**

- **FastAPI + static CesiumJS frontend** — cleanest 3D-globe integration and
  the best showcase of the visualization skill the project targets. The same
  app serves the API and the frontend (no separate build step).
- **Precompute pipeline** — a CLI step produces the orbit tracks and events as
  JSON; the app only loads and renders them. This keeps the UI instant and lets
  a GitHub Action refresh data on a schedule.
- **Starlink subset (~200 objects)** — keeps the O(n²) pairwise analysis
  (~20k pairs per timestep) trivially vectorizable without spatial pruning.

---

## Project layout

```
config.py                 # all knobs: group, time window, threshold, etc.
pipeline/
  fetch.py                # CelesTrak TLE fetch + parse + cache
  propagate.py            # sgp4 → TEME → ECEF (meters) + lat/lon/alt
  analysis.py             # vectorized pairwise distance + relative velocity
  run_pipeline.py         # orchestrate fetch → propagate → analyze → JSON
api/main.py               # FastAPI: static frontend + JSON endpoints
frontend/                 # index.html, app.js, style.css (CesiumJS via CDN)
data/                     # generated JSON outputs (raw cache is gitignored)
.github/workflows/refresh.yml  # cron refresh of orbital data
```

---

## Setup

```bash
git clone <this-repo>
cd satellite-trackr
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Run the pipeline

```bash
python pipeline/run_pipeline.py
```

This fetches the `starlink` group from CelesTrak (capped to 200 satellites),
propagates each over a 24h window at 1-minute steps, detects close approaches,
and writes `data/orbits/orbits.json` and `data/events/events.json`. A summary
is printed to the console.

### Run the app

```bash
uvicorn api.main:app --reload
# open http://localhost:8000
```

You should see the 3D globe with ~200 satellites moving along their orbits as
the timeline scrubs, red markers + connecting lines for close-approach pairs,
and an alerts panel. Drag the threshold slider to filter alerts.

---

## Configuration

All knobs live in `config.py` and most are overridable via environment
variables:

| Variable | Default | Meaning |
|---|---|---|
| `SAT_GROUP` | `starlink` | CelesTrak group |
| `N_MAX` | `200` | cap satellites (keeps O(n²) cheap) |
| `TIME_WINDOW_HRS` | `24` | propagation span |
| `STEP_MIN` | `1` | minutes between samples |
| `THRESHOLD_KM` | `50` | close-approach distance cutoff |
| `MIN_CLOSING_VEL_KM_S` | `0` | ignore slow pairs (0 = disabled) |
| `CESIUM_ION_TOKEN` | _(empty)_ | optional; app works without it |

### ⚠️ A note on the threshold

Genuine sub-10 km conjunctions among a ~200-satellite subset over 24h are
**rare**. The demo default of `THRESHOLD_KM=50` is set higher so the UI surfaces
visible "close approaches" to visualize. The detection math is identical at
any cutoff — lower it to `10` for realistic conjunction screening. The
relative closing speed (`rel_vel_km_s`) is included so fast approaches can be
prioritized.

---

## API reference

| Endpoint | Description |
|---|---|
| `GET /` | Frontend (`index.html`) |
| `GET /api/config` | Pipeline config + fetch timestamp |
| `GET /api/satellites` | `[{sat_id, name}, …]` |
| `GET /api/orbits?ids=a,b` | Full orbits payload (optionally filtered by id) |
| `GET /api/events?threshold_km=&min_rel_vel=` | Flagged events, filterable |
| `GET /api/refresh` | Re-read JSON from disk (dev convenience) |

```bash
curl localhost:8000/api/events?threshold_km=30
```

---

## How the math works

**Propagation.** Each TLE is loaded into an `sgp4.api.Satrec` and propagated with
`sgp4_array` across the time grid, returning TEME (true-equator mean-equinox)
positions/velocities in km. Each timestep's position is rotated into the
Earth-fixed frame (ECEF) using the Greenwich mean sidereal time (GMST, IAU 1982
formula), then scaled to meters — the `Cartesian3` values CesiumJS renders.

**Close-approach detection.** Per timestep, all pairwise differences are formed
via numpy broadcasting (`P[:,None,:] - P[None,:,:]`) and the Euclidean distance
computed in one vectorized call. Pairs under the threshold (and optionally
above a minimum relative closing speed) are recorded. The whole loop is
timestep-iterative (~1440 iterations, each a `(N,N,3)` tensor ≈ 1 MB), so it
never materializes the full `(T,N,N,3)` array.

**Relative velocity.** The magnitude of the pairwise velocity difference
indicates how urgently a pair is closing — surfaced alongside distance in
every event.

---

## Deploy

The app is a single FastAPI process that serves both the API and the static
frontend, so it deploys as one unit.

- **Render:** create a Web Service from this repo. Build: `pip install -r
  requirements.txt`. Start: `uvicorn api.main:app --host 0.0.0.0 --port $PORT`.
  Run the pipeline once on deploy (or rely on the GitHub Action to commit fresh
  data).
- **Scheduled refresh:** the `.github/workflows/refresh.yml` GitHub Action runs
  the pipeline every 6 hours and commits updated `data/*.json` back to the
  repo, so the demo stays fresh without a long-running compute job.

---

## What I learned

- Cleaning and ingesting messy public orbital data (CelesTrak TLEs go stale;
  parsing the 3-line format; dedup by NORAD catalog number).
- The SGP4 propagation model and the importance of frame conversion (TEME →
  ECEF) so satellites align correctly with a rotating Earth on a 3D globe.
- Vectorizing O(n²) pairwise distance with numpy broadcasting while keeping
  memory bounded by looping over timesteps.
- Wrapping a Python pipeline behind a FastAPI API and a static CesiumJS
  frontend, and keeping the visualization decoupled from the compute via
  precomputed JSON.

---

## License

MIT. Satellite data © CelesTrak / Space-Track.