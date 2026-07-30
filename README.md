# Satellite Collision Risk Tracker 🛰️

Pulls **live satellite orbital data** from
[CelesTrak](https://celestrak.org), calculates orbits with the sgp4
algorithm, computes close-approach ("collision risk") events between
satellites using vectorized numpy, and visualizes them on an interactive
CesiumJS 3D globe, wrapped in FastAPI.

Low Earth orbit is increasingly congested. Operators need tools to identify
when two objects are approaching each other dangerously close so they can plan
avoidance maneuvers. This project is a simplified version of that
conjunction-analysis tool, built on publicly available data.

---

## Architecture

```
pipeline (CLI / GitHub Action, every 6h)
  fetch TLEs (CelesTrak) ──▶ data/raw/*.tle
  run sgp4     ──▶ data/orbits/orbits.json  (ECEF xyz, meters)
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

- **FastAPI + static CesiumJS frontend**: 3D-globe integration with the ablity to showcase the visualization skill the project targets. This is a fullstack app, so both the frontend and backend are served together
- **Precompute pipeline** —  produces the orbit tracks and events as
  JSON, the app only loads and renders them. This lets the UI not be bogged down and lets
  a GitHub Action refresh data on a schedule.
- **Multi-group bulk fetch + spatial pruning** — `pipeline/fetch.py` can pull
  several CelesTrak groups and merge them; `pipeline/analysis.py` uses a
  `scipy.spatial.cKDTree` so the close-approach search stays O(n log n) instead
  of O(n²). An optional output decimation keeps the frontend JSON payload
  manageable when scaling to thousands of satellites.


**Limitations**
- Celestrak has a low rate limit. The pipeline mitigates this with cache reuse,
  polite delays between group fetches, and by letting GitHub Actions do the
  live fetching. It still respects CelesTrak's terms — no proxy rotation or
  header tricks.

- i need to dedupe potential collisions with the same satellites at very close times. 

- The UI sucks in general (I hate frontend), and is not at all mobile friendly. I will work on it

- I am not an astrophysicist, and have never claimed to be. The sgp4 algo has its limitations, so the orbit calculation is not perfect, but it's the best approximation I could find without going to grad school. 

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

### Run the precompute pipeline

```bash
python pipeline/run_pipeline.py
```

This fetches the `starlink` group from CelesTrak (capped to 200 satellites),
propagates each over a 24h window at 1-minute steps, detects close approaches,
and writes `data/orbits/orbits.json` and `data/events/events.json`. A summary of 'collision risk' events 
is printed to the console.

### Run the app

```bash
uvicorn api.main:app --reload
# open http://localhost:8000
```

You should see the 3D globe with ~200 satellites moving along their orbits as
the timeline scrubs, red markers + connecting lines for close-approach pairs,
and an alerts panel. Drag the threshold slider to filter alerts by near miss distance.

---

## Configuration

All controls live in `config.py` and most are overridable via environment
variables:

| Variable | Default | Meaning |
|---|---|---|
| `SAT_GROUP` | `starlink` | fallback single CelesTrak group |
| `SAT_GROUPS` | `SAT_GROUP` | comma-separated list of groups, e.g. `starlink,oneweb,iridium` |
| `N_MAX` | _(unset)_ | optional cap on total satellites; empty means no cap |
| `TIME_WINDOW_HRS` | `24` | propagation span |
| `STEP_MIN` | `1` | minutes between samples for analysis |
| `OUTPUT_STEP_MIN` | `5` | minutes between output samples for the frontend JSON; larger values reduce payload size |
| `THRESHOLD_KM` | `10` | close-approach distance cutoff |
| `MIN_CLOSING_VEL_KM_S` | `0` | ignore slow pairs (0 = disabled) |
| `FETCH_DELAY_S` | `1.0` | polite sleep between live CelesTrak group fetches |
| `CACHE_TTL_HRS` | `6.0` | reuse cached TLEs if newer than this |
| `CESIUM_ION_TOKEN` | _(empty)_ | optional; app works without it |

### ⚠️ A note on the threshold

Genuine sub-10 km conjunctions among a small satellite subset over 24h are
**rare**, but with thousands of satellites in a group like Starlink they are
much more common. The default `THRESHOLD_KM=10` is a realistic operational
screening cutoff. Raise it to `50` if you want the UI to surface more visible
"close approaches" for a sparse demo. The detection math is identical at any
cutoff — the relative closing speed (`rel_vel_km_s`) is included so fast
approaches can be prioritized.

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