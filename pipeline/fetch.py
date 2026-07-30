"""Fetch and parse TLE sets from CelesTrak.

Pulls the classic 3-line TLE format for one or more satellite groups, parses
it into (name, line1, line2) records, caches the raw text with a fetch
timestamp, deduplicates globally by NORAD catalog number, and applies an
optional cap.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from config import (
    CACHE_TTL_HRS,
    CELESTRAK_URL,
    FETCH_DELAY_S,
    N_MAX,
    RAW_DIR,
    SAT_GROUPS,
)

log = logging.getLogger(__name__)


def _cache_path(group: str, stamp: str) -> Path:
    return RAW_DIR / f"{group}_{stamp}.tle"


def _list_group_caches(group: str) -> list[Path]:
    """Return cached TLE files for a group, newest last."""
    return sorted(RAW_DIR.glob(f"{group}_*.tle"))


def _cache_is_fresh(path: Path, ttl_hours: float) -> bool:
    """True if the cache file was written within ``ttl_hours``."""
    if ttl_hours <= 0:
        return False
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return False
    return datetime.now(timezone.utc) - mtime < timedelta(hours=ttl_hours)


def fetch_raw_tles(group: str) -> tuple[str, str]:
    """Download raw TLE text from CelesTrak for a single group.

    Returns the raw text and the URL it came from. Retries with backoff on
    transient failures; CelesTrak rejects the default ``python-requests``
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
    raise RuntimeError(f"Could not fetch TLEs for '{group}' after retries: {last_err}")


def cache_raw(raw: str, group: str) -> Path:
    """Write the raw TLE text to data/raw/ with a UTC timestamp filename."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = _cache_path(group, stamp)
    path.write_text(raw, encoding="utf-8")
    log.info("Cached raw TLEs -> %s", path)
    return path


def _load_cached_group(group: str) -> str:
    """Return the newest cached raw TLE text for a group, or raise if none."""
    caches = _list_group_caches(group)
    if not caches:
        raise RuntimeError(f"No cached TLEs found for group '{group}' in data/raw/.")
    log.info("Using cached TLEs for %s: %s", group, caches[-1])
    return caches[-1].read_text(encoding="utf-8")


def _fetch_one_group(group: str, force_live: bool = False) -> str:
    """Fetch a single group, using cache if allowed and fresh.

    Falls back to the newest cache if the live fetch fails.
    """
    caches = _list_group_caches(group)
    if not force_live and caches and _cache_is_fresh(caches[-1], CACHE_TTL_HRS):
        log.info("Cache for %s is fresh (TTL=%.1fh); skipping live fetch.", group, CACHE_TTL_HRS)
        return caches[-1].read_text(encoding="utf-8")

    try:
        raw, _ = fetch_raw_tles(group)
        cache_raw(raw, group)
        return raw
    except Exception as exc:
        if caches:
            log.warning("Live fetch for %s failed (%s); falling back to cache.", group, exc)
            return caches[-1].read_text(encoding="utf-8")
        raise RuntimeError(
            f"Live fetch failed for '{group}' and no cached TLEs found. "
            "Run once while CelesTrak is reachable to seed a cache."
        ) from exc


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


def fetch_tles(
    groups: list[str] | None = None,
    n_max: int | None = N_MAX,
) -> list[dict]:
    """Fetch, cache, parse, dedupe, and optionally cap satellites.

    ``groups`` defaults to ``config.SAT_GROUPS``. Each group is fetched once
    (or read from cache), parsed, then merged and deduplicated by NORAD catalog
    number. ``n_max`` limits the final number of satellites if set; ``None``
    keeps everything.
    """
    groups = groups or SAT_GROUPS
    if not groups:
        raise ValueError("No CelesTrak groups configured.")

    all_records: list[dict] = []
    for idx, group in enumerate(groups):
        raw = _fetch_one_group(group)
        group_records = parse_tles(raw)
        log.info("Parsed %d TLE records from group=%s", len(group_records), group)
        all_records.extend(group_records)
        # Polite pause between live fetches (skip after the last group).
        if idx < len(groups) - 1:
            time.sleep(FETCH_DELAY_S)

    # Deduplicate by NORAD number across all groups, keeping first occurrence.
    seen: set[str] = set()
    unique: list[dict] = []
    for rec in all_records:
        satno = rec["satno"]
        if satno in seen:
            continue
        seen.add(satno)
        rec["sat_id"] = satno
        unique.append(rec)

    if n_max is not None and len(unique) > n_max:
        log.info("Capping %d satellites to N_MAX=%d", len(unique), n_max)
        unique = unique[:n_max]

    log.info(
        "Returning %d unique satellites from groups=%s",
        len(unique),
        ",".join(groups),
    )
    return unique
