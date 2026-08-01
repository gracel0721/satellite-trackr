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

import numpy as np

# Make `config` importable when run as a script (not just as a module).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import EVENTS_FILE, ORBITS_FILE, OUTPUT_STEP_MIN, SAT_GROUPS, STEP_MIN  # noqa: E402
from pipeline.analysis import detect_close_approaches  # noqa: E402
from pipeline.fetch import fetch_tles  # noqa: E402
from pipeline.propagate import propagate  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("pipeline")


def _decimation_factor() -> int:
    """Return the integer step between output samples.

    ``OUTPUT_STEP_MIN`` must be a multiple of ``STEP_MIN``; if it isn't we fall
    back to full resolution (factor 1) to avoid off-by-one surprises.
    """
    if OUTPUT_STEP_MIN <= STEP_MIN:
        return 1
    if OUTPUT_STEP_MIN % STEP_MIN != 0:
        log.warning(
            "OUTPUT_STEP_MIN (%d) is not a multiple of STEP_MIN (%d); "
            "using full resolution for output.",
            OUTPUT_STEP_MIN,
            STEP_MIN,
        )
        return 1
    return OUTPUT_STEP_MIN // STEP_MIN


def _decimate_ecef(ecef_m: list, factor: int) -> list:
    """Decimate a flat ``[x0,y0,z0,x1,y1,z1,...]`` list by ``factor``."""
    arr = np.array(ecef_m, dtype=np.int32).reshape(-1, 3)
    decimated = arr[::factor]
    return decimated.reshape(-1).tolist()


def _prepare_output(
    orbits: list[dict],
    events: list[dict],
    t_list: list[str],
    factor: int,
) -> tuple[list[dict], list[dict], list[str]]:
    """Build the frontend payload, optionally decimating the time grid.

    Events carry their own ECEF positions (embedded by analysis), so they no
    longer need to align to the orbit grid — every detected conjunction is kept
    regardless of the decimation factor. Only the orbit samples are decimated.
    """
    if factor <= 1:
        orbits_out = [
            {"sat_id": o["sat_id"], "name": o["name"], "ecef_m": o["ecef_m"]}
            for o in orbits
        ]
        return orbits_out, events, t_list

    out_t = t_list[::factor]
    orbits_out = [
        {
            "sat_id": o["sat_id"],
            "name": o["name"],
            "ecef_m": _decimate_ecef(o["ecef_m"], factor),
        }
        for o in orbits
    ]
    log.info(
        "Decimated orbit grid by factor=%d: %d timesteps, %d events (kept all)",
        factor,
        len(out_t),
        len(events),
    )
    return orbits_out, events, out_t


def run(groups: list[str] = SAT_GROUPS) -> dict:
    """Execute the pipeline end to end and write outputs. Returns a summary."""
    records = fetch_tles(groups=groups)
    orbits, t_list, start_iso = propagate(records)
    events = detect_close_approaches(orbits, t_list)

    ORBITS_FILE.parent.mkdir(parents=True, exist_ok=True)
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)

    factor = _decimation_factor()
    orbits_out, events_out, out_t = _prepare_output(orbits, events, t_list, factor)

    group_label = ",".join(groups)
    payload = {
        "groups": groups,
        "group": group_label,
        "fetch_time": start_iso,
        "n_satellites": len(orbits),
        "n_timesteps": len(out_t),
        "t": out_t,
        "orbits": orbits_out,
    }
    ORBITS_FILE.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    EVENTS_FILE.write_text(
        json.dumps(
            {"groups": groups, "group": group_label, "fetch_time": start_iso, "events": events_out},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    summary = {
        "groups": groups,
        "group": group_label,
        "n_satellites": len(orbits),
        "n_timesteps": len(out_t),
        "n_events": len(events_out),
        "orbits_file": str(ORBITS_FILE),
        "events_file": str(EVENTS_FILE),
    }
    log.info("Pipeline complete: %s", summary)
    return summary


if __name__ == "__main__":
    run()
