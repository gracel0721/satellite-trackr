"""Close-approach (conjunction) detection.

For each timestep, computes all pairwise Euclidean distances between
satellite positions using numpy broadcasting (vectorized within a timestep),
flags pairs under a configurable distance threshold, and records the
relative closing speed. Emits a flat, sortable event list.
"""
from __future__ import annotations

import logging

import numpy as np

from config import FLAG_REL_VEL, MIN_CLOSING_VEL_KM_S, THRESHOLD_KM

log = logging.getLogger(__name__)


def detect_close_approaches(orbits: list[dict], t_list: list[str]) -> list[dict]:
    """Flag all close approaches across the time window.

    ``t_list`` is the shared time grid (one ISO string per timestep). Each
    satellite's ``ecef_m`` / ``vel_teme_km_s`` is expected to align to that grid
    (length T*3); satellites that dropped timesteps during propagation are
    skipped.

    Returns a list of event dicts sorted by distance (km) ascending::

        {sat_a, sat_b, name_a, name_b, timestamp, distance_km, rel_vel_km_s}
    """
    if len(orbits) < 2:
        log.warning("Fewer than 2 satellites; no close approaches possible.")
        return []

    global_t = t_list
    ids = [o["sat_id"] for o in orbits]
    names = {o["sat_id"]: o["name"] for o in orbits}
    N = len(orbits)
    T = len(global_t)

    # P: (T, N, 3) meters, V: (T, N, 3) km/s (relative-speed magnitude is
    # frame-insensitive enough for demo purposes; TEME is near-inertial).
    P = np.full((T, N, 3), np.nan, dtype=float)
    V = np.full((T, N, 3), np.nan, dtype=float)
    for j, o in enumerate(orbits):
        ecef = np.array(o["ecef_m"], dtype=float).reshape(-1, 3)
        v = np.array(o["vel_teme_km_s"], dtype=float).reshape(-1, 3)
        if ecef.shape[0] != T:
            log.warning("Sat %s has %d steps != %d; skipping", o["sat_id"], ecef.shape[0], T)
            continue
        P[:, j, :] = ecef
        V[:, j, :] = v

    iu, ju = np.triu_indices(N, k=1)  # upper-triangle pair indices (i<j)
    events: list[dict] = []
    for ti in range(T):
        Pt = P[ti]  # (N, 3)
        if np.isnan(Pt).any():
            good = ~np.isnan(Pt[:, 0])
            # Only compare pairs where both members are present at this step.
            a_m, b_m = np.where(good)[0], np.where(good)[0]
            if good.sum() < 2:
                continue
            mask = good[iu] & good[ju]
            pi, pj = iu[mask], ju[mask]
        else:
            pi, pj = iu, ju
        diff = Pt[pi] - Pt[pj]              # (M, 3) meters
        dist_m = np.sqrt(np.einsum("ij,ij->i", diff, diff))
        dist_km = dist_m / 1000.0
        under = dist_km <= THRESHOLD_KM
        if not under.any():
            continue
        # Relative closing speed for flagged pairs.
        vdiff = V[ti][pi[under]] - V[ti][pj[under]]
        rel_vel = np.sqrt(np.einsum("ij,ij->i", vdiff, vdiff))
        if MIN_CLOSING_VEL_KM_S > 0:
            keep = rel_vel >= MIN_CLOSING_VEL_KM_S
            dist_km_f = dist_km[under][keep]
            rel_vel_f = rel_vel[keep]
            pi_f = pi[under][keep]
            pj_f = pj[under][keep]
        else:
            dist_km_f = dist_km[under]
            rel_vel_f = rel_vel
            pi_f = pi[under]
            pj_f = pj[under]
        for k in range(len(dist_km_f)):
            a, b = int(pi_f[k]), int(pj_f[k])
            events.append(
                {
                    "sat_a": ids[a],
                    "sat_b": ids[b],
                    "name_a": names[ids[a]],
                    "name_b": names[ids[b]],
                    "timestamp": global_t[ti],
                    "distance_km": round(float(dist_km_f[k]), 3),
                    "rel_vel_km_s": round(float(rel_vel_f[k]), 3) if FLAG_REL_VEL else None,
                }
            )

    # Deduplicate identical (pair, timestamp) and sort by distance ascending.
    seen = set()
    unique_events: list[dict] = []
    for ev in events:
        key = (ev["sat_a"], ev["sat_b"], ev["timestamp"])
        if key in seen:
            continue
        seen.add(key)
        unique_events.append(ev)
    unique_events.sort(key=lambda e: e["distance_km"])
    log.info(
        "Detected %d close approaches (threshold=%.1f km) over %d timesteps",
        len(unique_events), THRESHOLD_KM, T,
    )
    return unique_events