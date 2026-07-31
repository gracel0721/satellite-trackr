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
pipeline (CLI locally; Cloud Function every 12h in prod)
  fetch TLEs (CelesTrak) ──▶ data/raw/*.tle
  run sgp4     ──▶ data/orbits/orbits.json  (ECEF xyz, meters)
  close-approach analysis ──▶ data/events/events.json
                 │
                 ▼
Cloud Function (Cloud Scheduler, every 12h) ──▶ public GCS bucket /positions.json
  (merged orbits + events; the frontend fetches this in production)
  (locally, `python pipeline/run_pipeline.py` writes data/*.json for dev)
                 │
                 ▼
FastAPI (api/main.py)  ──▶ local dev: /api/config, /api/orbits, /api/events (reads repo data)
Vercel (api/main.py)   ──▶ prod: /api/* via Vercel auto-detection (api/main.py defines `app`);
                           /api/config returns the GCS `data_url` (slim FastAPI-only deps)
                 ▼
CesiumJS frontend (frontend/index.html + app.js)
  fetches positions.json from GCS when `data_url` is set, else the API endpoints
  3D globe · moving satellite dots · time-scrub (Cesium clock + timeline)
  red close-approach pairs + connecting lines · alerts panel · threshold slider
```

**Design choices**

- **FastAPI + static CesiumJS frontend**: 3D-globe integration with the ablity to showcase the visualization skill the project targets. This is a fullstack app, so both the frontend and backend are served together
- **Precompute pipeline** —  produces the orbit tracks and events as
  JSON, the app only loads and renders them. This lets the UI not be bogged down and lets
  a Cloud Function refresh data on a schedule.
- **Multi-group bulk fetch + spatial pruning** — `pipeline/fetch.py` can pull
  several CelesTrak groups and merge them; `pipeline/analysis.py` uses a
  `scipy.spatial.cKDTree` so the close-approach search stays O(n log n) instead
  of O(n²). An optional output decimation keeps the frontend JSON payload
  manageable when scaling to thousands of satellites.



**Limitations**
- Celestrak has a low rate limit. The pipeline mitigates this with cache reuse,
  polite delays between group fetches, and by running the live fetch on a Cloud
  Function on a schedule (every 12h) rather than per request.

- The UI sucks in general (I hate frontend), and is not at all mobile friendly. I will work on it

- I am not an astrophysicist, and have never claimed to be. The sgp4 algo has its limitations, so the orbit calculation is not perfect, but it's the best approximation I could find without going to grad school. 

---

## Project layout

``` 
config.py                 # all knobs: group, time window, threshold, etc.
pipeline/
  fetch.py                # CelesTrak TLE fetch + parse + GCS-backed cache
  propagate.py            # sgp4 → TEME → ECEF (meters) + lat/lon/alt
  analysis.py             # cKDTree close-approach detection + relative velocity
  run_pipeline.py         # orchestrate fetch → propagate → analyze → JSON
api/main.py               # FastAPI /api/* (local dev: full app + static frontend; Vercel: /api/* via auto-detection)
main.py                   # Cloud Function: run pipeline + publish positions.json to GCS
pyproject.toml            # Vercel Python backend: slim FastAPI deps (Vercel auto-detects api/main.py)
frontend/                 # index.html, app.js, style.css (CesiumJS via CDN)
data/                     # generated JSON outputs (raw cache is gitignored)
infra/                    # Terraform: Cloud Function + Cloud Scheduler + GCS bucket
vercel.json               # Vercel: static frontend outputDirectory
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
| `DATA_URL` | _(empty)_ | public GCS `positions.json` URL; when set (Vercel deploy) the frontend reads data from the bucket instead of the repo's committed files |

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

**Close-approach detection.** Per timestep, satellite positions are indexed in
a `scipy.spatial.cKDTree` and all pairs within the threshold are queried in
O(n log n) rather than an O(n²) all-pairs broadcast. Exact Euclidean distance
and relative closing speed are then computed only for those candidate pairs.
Pairs are recorded the first timestep a pair crosses into the threshold (a
"breach"), so a sustained close approach emits one event instead of one per
timestep. The whole loop is timestep-iterative (~1440 iterations), so it
never materializes the full `(T,N,N,3)` array.

**Relative velocity.** The magnitude of the pairwise velocity difference
indicates how urgently a pair is closing — surfaced alongside distance in
every event.

---

## Deploy

In production the static CesiumJS frontend is served by **Vercel** and reads
data straight from the public GCS bucket. A single FastAPI serverless function
(`api/main.py`, declared as the entrypoint in `pyproject.toml`) serves
`/api/config`, which hands the frontend the bucket URL.

- **Vercel:** import this repo. No build step — `vercel.json` sets
  `outputDirectory: "frontend"`. Vercel auto-detects the FastAPI app in
  `api/main.py` (it defines the top-level `app`) and serves its `/api/*`
  routes; `pyproject.toml` carries only the slim runtime deps (just `fastapi`).
  The heavy `requirements.txt` is for the Cloud Function and is excluded from
  the Vercel build via `.vercelignore`.
  In the Vercel dashboard set the `DATA_URL` environment variable to the public
  GCS `positions.json` URL (Terraform outputs it as `positions_json_url`);
  optionally set `CESIUM_ION_TOKEN`. With `DATA_URL` set the frontend fetches
  `positions.json` from GCS; without it (local dev) it falls back to the
  FastAPI `/api/orbits` + `/api/events` endpoints reading the repo's committed
  `data/*.json`.
- **Local dev:** `uvicorn api.main:app --reload` serves the same frontend plus
  the `/api/*` endpoints that read `data/*.json` from disk (leave `DATA_URL`
  unset).
- **Scheduled refresh:** the Cloud Function (deployed via `infra/`, triggered by
  Cloud Scheduler every 12 hours) runs the pipeline and publishes
  `positions.json` to the public GCS bucket the frontend reads in production.
  The committed `data/*.json` is just a static snapshot for local dev; refresh
  it manually with `python pipeline/run_pipeline.py`.

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