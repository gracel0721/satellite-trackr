"""Fetch and parse TLE sets from CelesTrak.

Pulls the classic 3-line TLE format for a satellite group, parses it into
(name, line1, line2) records, caches the raw text with a fetch timestamp, and
caps to N_MAX satellites.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from config import CELESTRAK_URL, N_MAX, RAW_DIR, SAT_GROUP

log = logging.getLogger(__name__)


def fetch_raw_tles(group: str = SAT_GROUP) -> tuple[str, str]:
    """Download raw TLE text from CelesTrak.

    Returns the raw text and the URL it came from. Retries with backoff on
    transient failures; CelesTrak rejects the default `python-requests`
    User-Agent, so we send a descriptive one.
    """
    url = CELESTRAK_URL.format(group=group)
    headers = {"User-Agent": "satellite-trackr/0.1 (portfolio project; contact: user@example.com)"}
    log.info("Fetching TLEs from %s", url)
    last_err = None
    for attempt in range(4):
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.text, url
        except requests.RequestException as exc:
            last_err = exc
            log.warning("Fetch attempt %d failed: %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not fetch TLEs after retries: {last_err}")


def parse_tles(raw: str) -> list[dict]:
    """Parse 3-line TLE records into dicts.

    Each record is::

        0  NAME
        1  1 NNNNN ...
        2  2 NNNNN ...

    Returns a list of ``{name, line1, line2, satno}``.
    """
    records: list[dict] = []
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    i = 0
    while i + 2 < len(lines):
        name = lines[i]
        l1, l2 = lines[i + 1], lines[i + 2]
        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            i += 1
            continue
        # NORAD catalog number sits in columns 3-7 of line 1.
        satno = l1[2:7].strip()
        records.append({"name": name, "line1": l1, "line2": l2, "satno": satno})
        i += 3
    return records


def cache_raw(raw: str, group: str = SAT_GROUP) -> str:
    """Write the raw TLE text to data/raw/ with a UTC timestamp filename."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RAW_DIR / f"{group}_{stamp}.tle"
    path.write_text(raw, encoding="utf-8")
    log.info("Cached raw TLEs -> %s", path)
    return str(path)


def fetch_tles(group: str = SAT_GROUP, n_max: int = N_MAX) -> list[dict]:
    """Fetch, cache, parse, and cap to ``n_max`` satellites.

    Adds a stable ``sat_id`` (NORAD catalog number) to each record. If the live
    fetch fails (CelesTrak rate limits / outages), falls back to the most
    recently cached raw file in data/raw/.
    """
    try:
        raw, url = fetch_raw_tles(group)
        cache_raw(raw, group)
    except Exception as exc:
        log.warning("Live fetch failed (%s); falling back to most recent cache.", exc)
        caches = sorted(RAW_DIR.glob(f"{group}_*.tle"))
        if not caches:
            raise RuntimeError(
                "Live fetch failed and no cached TLEs found in data/raw/. "
                "Run once while CelesTrak is reachable to seed a cache."
            ) from exc
        raw = caches[-1].read_text(encoding="utf-8")
        log.warning("Using cached TLEs: %s", caches[-1])
    records = parse_tles(raw)
    # Deduplicate by NORAD number, keeping first occurrence.
    seen, unique = set(), []
    for rec in records:
        if rec["satno"] in seen:
            continue
        seen.add(rec["satno"])
        rec["sat_id"] = rec["satno"]
        unique.append(rec)
    if len(unique) > n_max:
        log.info("Capping %d satellites to N_MAX=%d", len(unique), n_max)
        unique = unique[:n_max]
    log.info("Parsed %d TLE records (group=%s)", len(unique), group)
    return unique