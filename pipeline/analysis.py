"""Close-approach (conjunction) detection.

For each timestep, finds candidate satellite pairs within a configurable
threshold using a spatial index (scipy.spatial.cKDTree), then computes exact
Euclidean distance and relative closing speed only for those candidates.
This avoids the O(n²) all-pairs broadcast and scales to thousands of
satellites.
"""
from __future__ import annotations

import logging

import numpy as np
from scipy.spatial import cKDTree

from config import FLAG_REL_VEL, MIN_CLOSING_VEL_KM_S, THRESHOLD_KM

log = logging.getLogger(__name__)


def detect_close_approaches(orbits: list[dict], t_list: list[str]) -> list[dict]:
    """Flag all close approaches across the time window.

    ``t_list`` is the shared time grid (one ISO string per timestep). Each
    satellite's ``ecef_m`` / ``vel_teme_km_s`` is expected to align to that grid
    (length T*3); satellites that dropped timesteps during propagation are
    skipped at the timesteps where they are missing.

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

    events: list[dict] = []
    threshold_m = THRESHOLD_KM * 1000.0
    # Tracks pairs that were already within threshold at the previous timestep.
    # A new "breach" is recorded only when a pair transitions from outside to
    # inside the threshold, so a single sustained close approach emits one event
    # (the first breach) instead of one per timestep.
    active_pairs_prev: set[tuple[int, int]] = set()

    for ti in range(T):
        Pt = P[ti]  # (N, 3) meters
        good = ~np.isnan(Pt[:, 0])
        n_good = good.sum()
        if n_good < 2:
            continue

        # Indices of satellites present at this timestep, and their positions.
        good_idx = np.where(good)[0]
        positions_km = Pt[good] / 1000.0  # cKDTree works in km for our threshold
        vt = V[ti][good]

        # Build a spatial index and ask for all pairs within the threshold.
        tree = cKDTree(positions_km)
        pairs = tree.query_pairs(r=THRESHOLD_KM, output_type="ndarray")
        if pairs.shape[0] == 0:
            continue

        # Map pair indices back to full-orbit indices.
        a_idx = good_idx[pairs[:, 0]]
        b_idx = good_idx[pairs[:, 1]]

        # Exact distance for the candidate pairs (meters -> km).
        diff = Pt[a_idx] - Pt[b_idx]
        dist_km = np.sqrt(np.einsum("ij,ij->i", diff, diff)) / 1000.0
        under = dist_km <= THRESHOLD_KM
        if not under.any():
            continue

        # Relative closing speed for flagged pairs.
        vdiff = vt[pairs[:, 0][under]] - vt[pairs[:, 1][under]]
        rel_vel = np.sqrt(np.einsum("ij,ij->i", vdiff, vdiff))

        if MIN_CLOSING_VEL_KM_S > 0:
            keep = rel_vel >= MIN_CLOSING_VEL_KM_S
            if not keep.any():
                continue
            dist_km_f = dist_km[under][keep]
            rel_vel_f = rel_vel[keep]
            a_idx_f = a_idx[under][keep]
            b_idx_f = b_idx[under][keep]
        else:
            dist_km_f = dist_km[under]
            rel_vel_f = rel_vel
            a_idx_f = a_idx[under]
            b_idx_f = b_idx[under]

        # Collect pairs that satisfy all filters at this timestep and map each
        # canonical pair to its distance/velocity.
        current_pairs: set[tuple[int, int]] = set()
        pair_info: dict[tuple[int, int], tuple[float, float]] = {}
        for k in range(len(dist_km_f)):
            a, b = int(a_idx_f[k]), int(b_idx_f[k])
            key = (a, b) if a < b else (b, a)
            current_pairs.add(key)
            pair_info[key] = (float(dist_km_f[k]), float(rel_vel_f[k]))

        # Emit an event only for pairs that just crossed into the threshold.
        new_pairs = current_pairs - active_pairs_prev
        for a, b in new_pairs:
            dist_km, rel_vel = pair_info[(a, b)]
            events.append(
                {
                    "sat_a": ids[a],
                    "sat_b": ids[b],
                    "name_a": names[ids[a]],
                    "name_b": names[ids[b]],
                    "timestamp": global_t[ti],
                    "distance_km": round(dist_km, 3),
                    "rel_vel_km_s": round(rel_vel, 3) if FLAG_REL_VEL else None,
                }
            )

        active_pairs_prev = current_pairs

    # Sort by distance ascending. (The previous exact-timestamp dedupe is no
    # longer needed because each contiguous close-approach window now yields a
    # single event, but we keep a safety dedupe just in case.)
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
