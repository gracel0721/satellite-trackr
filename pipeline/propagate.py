"""Orbit propagation: TLE -> TEME positions/velocities -> ECEF (meters).

Uses ``sgp4.api.Satrec.sgp4_array`` to propagate each TLE across the time
window in one vectorized call, then rotates TEME (true equator mean equinox)
positions into the Earth-fixed frame CesiumJS consumes (ECEF Cartesian3 in
meters) using the Greenwich mean sidereal time (GMST) angle.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

import numpy as np
from sgp4.api import Satrec, jday

from config import STEP_MIN, TIME_WINDOW_HRS

log = logging.getLogger(__name__)

KM_TO_M = 1000.0


def _gmst_rad(jd_ut1: float) -> float:
    """Greenwich mean sidereal time in radians for a Julian date (UT1≈UTC).

    IAU 1982 formula (Vallado, *Fundamentals of Astrodynamics*). Good to well
    under an arcminute over a 24h window, which is fine for visualization.
    """
    tut = (jd_ut1 - 2451545.0) / 36525.0
    # GMST in seconds of time.
    gmst_sec = (
        67310.54841
        + (876600.0 * 3600 + 8640184.812866) * tut
        + 0.093104 * tut * tut
        - 6.2e-6 * tut * tut * tut
    )
    # Seconds of time -> degrees (1 sec time = 1/240 deg), -> radians, mod 2pi.
    gmst_deg = (gmst_sec / 240.0) % 360.0
    return math.radians(gmst_deg)


def teme_to_ecef(pos_teme_km: np.ndarray, gmst_rad: float) -> np.ndarray:
    """Rotate a TEME position (km) into ECEF (km) using the GMST angle.

    Works on a single (3,) vector or an array of shape (T, 3).
    """
    x, y, z = pos_teme_km[..., 0], pos_teme_km[..., 1], pos_teme_km[..., 2]
    cos_g, sin_g = math.cos(gmst_rad), math.sin(gmst_rad)
    out = np.empty_like(pos_teme_km)
    out[..., 0] = cos_g * x + sin_g * y
    out[..., 1] = -sin_g * x + cos_g * y
    out[..., 2] = z
    return out


def ecef_to_lla(ecef_m: np.ndarray) -> np.ndarray:
    """Convert ECEF meters to geodetic lat(deg)/lon(deg)/alt(km).

    Bowring closed form (WGS84). Only used for altitude labeling; Cesium
    itself renders directly from the ECEF xyz.
    """
    a = 6378137.0
    b = 6356752.3142
    e2 = 1.0 - (b / a) ** 2
    ep2 = (a / b) ** 2 - 1.0
    x, y, z = ecef_m[..., 0], ecef_m[..., 1], ecef_m[..., 2]
    p = np.sqrt(x * x + y * y)
    th = np.arctan2(a * z, b * p)
    lon = np.arctan2(y, x)
    lat = np.arctan2(z + ep2 * b * np.sin(th) ** 3, p - e2 * a * np.cos(th) ** 3)
    N = a / np.sqrt(1.0 - e2 * np.sin(lat) ** 2)
    alt = p / np.cos(lat) - N
    return np.stack([np.degrees(lat), np.degrees(lon), alt / 1000.0], axis=-1)


def _time_grid(start: datetime) -> list[datetime]:
    """Build the propagation time grid from `start` over TIME_WINDOW_HRS."""
    n_steps = TIME_WINDOW_HRS * 60 // STEP_MIN
    return [start + timedelta(minutes=i * STEP_MIN) for i in range(n_steps + 1)]


def _jd_fr(dt: datetime) -> tuple[float, float]:
    """Julian day + fractional day (UT1≈UTC) for a timezone-aware datetime."""
    return jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second + dt.microsecond * 1e-6)


def propagate(records: list[dict], start: datetime | None = None) -> tuple[list[dict], str]:
    """Propagate all TLE records across the time window.

    Returns ``(orbit_records, start_iso)`` where each orbit record is::

        {sat_id, name, t: [iso], ecef_m: [[x,y,z,...]], lla: [[lat,lon,alt_km,...]],
         vel_teme_km_s: [[vx,vy,vz,...]]}

    ``ecef_m`` is a flat float list (meters) of x,y,z repeated per timestep,
    matching how the frontend builds Cesium Cartesian3 samples.
    """
    start = start or datetime.now(timezone.utc)
    times = _time_grid(start)
    jds = np.empty(len(times))
    frs = np.empty(len(times))
    for i, t in enumerate(times):
        jds[i], frs[i] = _jd_fr(t)
    gmsts = np.array([_gmst_rad(jd + fr) for jd, fr in zip(jds, frs)])
    iso_times = [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in times]

    orbits: list[dict] = []
    for rec in records:
        sat = Satrec.twoline2rv(rec["line1"], rec["line2"])
        # sgp4_array returns r, v with shape (T, 3) in km / km/s (TEME).
        err, r, v = sat.sgp4_array(jds, frs)
        if not np.all(err == 0):
            good = err == 0
            if good.sum() < 2:
                log.warning("Sat %s mostly failed to propagate (skipping)", rec["sat_id"])
                continue
            # Keep only good timesteps for this satellite.
            r = r[good]
            v = v[good]
            gmst_used = gmsts[good]
        else:
            gmst_used = gmsts
        r_teme = r  # (T, 3) km
        v_teme = v  # (T, 3) km/s
        # Rotate each timestep into ECEF using its GMST.
        ecef_km = np.empty_like(r_teme)
        for i, g in enumerate(gmst_used):
            ecef_km[i] = teme_to_ecef(r_teme[i], g)
        # Integer meters — compact for JSON and sub-meter precision is plenty.
        ecef_m = (ecef_km * KM_TO_M).round(0).astype(np.int32)

        orbits.append(
            {
                "sat_id": rec["sat_id"],
                "name": rec["name"],
                # Positions for the frontend (meters) and velocity for the
                # analysis step (kept in memory only — not written to JSON).
                "ecef_m": ecef_m.reshape(-1).tolist(),
                "vel_teme_km_s": v_teme.round(4).tolist(),
            }
        )
    log.info("Propagated %d/%d satellites over %d timesteps", len(orbits), len(records), len(times))
    return orbits, iso_times, start.strftime("%Y-%m-%dT%H:%M:%SZ")