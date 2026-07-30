# Satellite Collision Risk Tracker

A portfolio project that pulls live satellite orbital data, calculates close-approach ("collision risk") events between satellites, and visualizes them on an interactive 3D globe.

## Problem Statement

Low Earth orbit is increasingly congested with active satellites, defunct satellites, and debris. Operators need tools to identify when two objects are approaching each other dangerously close so they can plan avoidance maneuvers. This project builds a simplified version of that kind of conjunction (collision) analysis tool, using public data.

## Goals

- Demonstrate real-world data engineering (ingesting and cleaning messy orbital data)
- Demonstrate applied math/physics (orbit propagation, distance calculations)
- Demonstrate data visualization skills (interactive 3D globe)
- Produce a demo-able, screen-shareable artifact for interviews

## Tech Stack

| Layer | Tool | Why |
|---|---|---|
| Data source | [CelesTrak](https://celestrak.org) TLE data | Free, public, updated multiple times daily |
| Orbit propagation | `sgp4` (Python library) | Industry-standard algorithm for converting TLEs into positions/velocities |
| Data processing | `pandas`, `numpy` | Already familiar; good fit for cleaning and analyzing time-series position data |
| Collision analysis | Custom Python (numpy vectorized distance calcs) | Core "hook" of the project |
| Visualization | [CesiumJS](https://cesium.com/platform/cesiumjs/) | Purpose-built 3D globe renderer for geospatial/orbital data |
| Dashboard/app layer | Streamlit (or a lightweight Flask/FastAPI + HTML/CesiumJS frontend) | Fast way to wrap Python logic into an interactive app |
| Hosting | Streamlit Community Cloud, Render, or GitHub Pages (for static frontend) | Free tier hosting for a portfolio demo |

## Project Phases

### Phase 1: Data Collection
- Pull TLE data from CelesTrak for a specific satellite group (e.g., "active", "starlink", or "debris") via their API/text endpoints.
- Store raw TLEs locally (CSV/JSON) with a timestamp of when they were fetched.
- Set up a scheduled refresh (e.g., every few hours) since TLEs go stale.

### Phase 2: Orbit Propagation
- Use `sgp4` to convert each TLE into satellite position (x, y, z) and velocity vectors at a series of time steps (e.g., every 1–5 minutes over a 24-hour window).
- Store results in a pandas DataFrame: `satellite_id, timestamp, x, y, z, vx, vy, vz`.

### Phase 3: Collision / Close-Approach Analysis
- For each time step, compute pairwise distances between all satellites (vectorized with numpy for performance — avoid naive O(n²) Python loops on large sets).
- Flag pairs whose distance drops below a configurable threshold (e.g., 5–10 km) as "close approaches."
- Optionally factor in relative velocity to prioritize alerts (fast relative closing speed = more urgent).
- Output a table of flagged events: `satellite_a, satellite_b, timestamp, distance_km, relative_velocity_km_s`.

### Phase 4: Visualization
- Render a 3D globe using CesiumJS.
- Plot satellite positions as moving dots along their orbits.
- Highlight flagged close-approach pairs (e.g., color them red, draw a connecting line, or show an alert panel).
- Allow time-scrubbing so a viewer can watch the risk window play out.

### Phase 5: Dashboard/App Wrapper
- Wrap the pipeline in a Streamlit app (Python-only, fastest to build) OR a small web frontend (HTML/JS + CesiumJS embed, backed by a Python API) if a richer UI is wanted.
- Add filters: satellite group, date/time range, distance threshold.
- Add a simple "alerts" list/table showing current flagged events.

## Stretch Goals (if time allows)

- Historical trend chart: how many close approaches per day/week for a given satellite group.
- Compare congestion across different orbital shells (LEO vs. MEO vs. GEO).
- Simple risk score combining distance + relative velocity + object size (if size data available).
- Deploy publicly with a live-updating dataset.

## Deliverables

- GitHub repo with clean README, setup instructions, and screenshots/GIF of the dashboard.
- Live demo link (if hosted).
- Short write-up (blog post or README section) explaining the problem, approach, and what you learned — useful for LinkedIn posts and interview talking points.

## Learning Resources to Start With

- CelesTrak documentation: https://celestrak.org/NORAD/documentation/
- `sgp4` Python package docs (PyPI)
- CesiumJS "Getting Started" guide: https://cesium.com/learn/
- Any GitHub repo tagged `tle` or `sgp4` for reference implementations
