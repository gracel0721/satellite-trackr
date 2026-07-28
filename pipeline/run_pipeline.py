"""Run the full pipeline: fetch -> propagate -> detect close approaches -> JSON.

Usage:
    python -m pipeline.run_pipeline
    python pipeline/run_pipeline.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Make `config` importable when run as a script (not just as a module).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import EVENTS_FILE, ORBITS_FILE, SAT_GROUP  # noqa: E402
from pipeline.analysis import detect_close_approaches  # noqa: E402
from pipeline.fetch import fetch_tles  # noqa: E402
from pipeline.propagate import propagate  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("pipeline")


def run(group: str = SAT_GROUP) -> dict:
    """Execute the pipeline end to end and write outputs. Returns a summary."""
    records = fetch_tles(group=group)
    orbits, t_list, start_iso = propagate(records)
    events = detect_close_approaches(orbits, t_list)

    ORBITS_FILE.parent.mkdir(parents=True, exist_ok=True)
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Shared time grid + per-satellite positions only (velocity stays in
    # memory for analysis and is not written out, keeping the JSON small).
    orbits_out = [
        {"sat_id": o["sat_id"], "name": o["name"], "ecef_m": o["ecef_m"]}
        for o in orbits
    ]
    payload = {
        "group": group,
        "fetch_time": start_iso,
        "n_satellites": len(orbits),
        "n_timesteps": len(t_list),
        "t": t_list,
        "orbits": orbits_out,
    }
    ORBITS_FILE.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    EVENTS_FILE.write_text(
        json.dumps({"group": group, "fetch_time": start_iso, "events": events}, separators=(",", ":")),
        encoding="utf-8",
    )
    summary = {
        "group": group,
        "n_satellites": len(orbits),
        "n_timesteps": len(t_list),
        "n_events": len(events),
        "orbits_file": str(ORBITS_FILE),
        "events_file": str(EVENTS_FILE),
    }
    log.info("Pipeline complete: %s", summary)
    return summary


if __name__ == "__main__":
    run()