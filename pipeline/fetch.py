"""Fetch and parse TLE sets from CelesTrak.

Pulls the classic 3-line TLE format for one or more satellite groups, parses
it into (name, line1, line2) records, caches the raw text with a fetch
timestamp, deduplicates globally by NORAD catalog number, and applies an
optional cap.
"""
from __future__ import annotations

import logging
import os
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


# --- GCS-backed TLE cache --------------------------------------------------
# Cloud Functions start with an empty /tmp, so a local-only cache is lost on
# every cold start and CelesTrak is often unreachable from GCP (connect
# timeouts). Persisting the last-good TLE set to the data bucket lets the
# function fall back to it when a live fetch fails, so refreshes keep working
# even when CelesTrak can't be reached. Set TLE_CACHE_BUCKET (or DATA_BUCKET)
# to enable; unset => no-op (local dev path). The google-cloud-storage import
# is lazy so this module loads even where the dep isn't installed (local dev).
_TLE_CACHE_BUCKET = os.environ.get("TLE_CACHE_BUCKET", "") or os.environ.get("DATA_BUCKET", "")
_gcs_client = None  # storage.Client, created on first use


class TLENotModifiedError(RuntimeError):
    """CelesTrak returned its 403 "GP data has not updated since…" guard.

    This is NOT a failure — CelesTrak is telling us the group's data is
    unchanged since our last successful download (it updates ~every 2h and
    blocks re-downloads in between). The caller should reuse its cache.
    """


def _gcs_blob(group: str):
    """Return the GCS blob for a group's cached TLEs, or None if caching off."""
    global _gcs_client
    if not _TLE_CACHE_BUCKET:
        return None
    if _gcs_client is None:
        from google.cloud import storage  # lazy: not needed for local dev
        _gcs_client = storage.Client()
    return _gcs_client.bucket(_TLE_CACHE_BUCKET).blob(f"tles/{group}.tle")


def _gcs_write_tle(raw: str, group: str) -> None:
    """Persist a freshly fetched TLE set to GCS (best-effort)."""
    blob = _gcs_blob(group)
    if blob is None:
        return
    try:
        blob.upload_from_string(raw, content_type="text/plain")
        log.info("Cached TLEs to GCS -> gs://%s/tles/%s.tle", _TLE_CACHE_BUCKET, group)
    except Exception as exc:  # never let a cache write kill the run
        log.warning("GCS TLE cache write failed (%s); continuing.", exc)


def _gcs_read_tle(group: str) -> str | None:
    """Return the GCS-cached TLE text for a group, or None if absent/off."""
    blob = _gcs_blob(group)
    if blob is None:
        return None
    try:
        return blob.download_as_text()
    except Exception as exc:
        log.warning("GCS TLE cache read failed (%s).", exc)
        return None


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
            # CelesTrak's anti-poll guard: a 403 whose body says the group data
            # hasn't changed since our last download. Treat as "use cache".
            if resp.status_code == 403 and "GP data has not updated" in resp.text:
                raise TLENotModifiedError(
                    f"CelesTrak reports '{group}' data unchanged since last download"
                )
            resp.raise_for_status()
            return resp.text, url
        except TLENotModifiedError:
            raise
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


def _fetch_one_group(group: str, force_live: bool = False) -> str:
    """Fetch a single group, using cache if allowed and fresh.

    Fallback chain on any live-fetch failure (timeout, 429, or the
    "not updated" guard): newest local cache (any age) → GCS-cached TLEs →
    raise. On a successful live fetch the TLEs are also persisted to GCS so
    later cold starts (empty /tmp) can still recover them.
    """
    caches = _list_group_caches(group)
    if not force_live and caches and _cache_is_fresh(caches[-1], CACHE_TTL_HRS):
        log.info("Cache for %s is fresh (TTL=%.1fh); skipping live fetch.", group, CACHE_TTL_HRS)
        return caches[-1].read_text(encoding="utf-8")

    try:
        raw, _ = fetch_raw_tles(group)
        cache_raw(raw, group)
        _gcs_write_tle(raw, group)  # persist for cross-cold-start reuse
        return raw
    except TLENotModifiedError:
        log.info("CelesTrak reports %s unchanged; reusing cache.", group)
    except Exception as exc:
        log.warning("Live fetch for %s failed (%s); falling back to cache.", group, exc)

    # Fallback 1: newest local cache (any age).
    if caches:
        log.info("Using local cached TLEs for %s: %s", group, caches[-1])
        return caches[-1].read_text(encoding="utf-8")

    # Fallback 2: GCS-cached TLEs (the path the Cloud Function relies on,
    # since /tmp is empty on every cold start).
    gcs_raw = _gcs_read_tle(group)
    if gcs_raw is not None:
        log.info("Using GCS-cached TLEs for %s; seeding local cache.", group)
        cache_raw(gcs_raw, group)
        return gcs_raw

    raise RuntimeError(
        f"Cannot obtain TLEs for '{group}': live fetch failed and no cached "
        "TLEs found (local or GCS). Seed the cache by running while "
        "CelesTrak is reachable, or upload to gs://<bucket>/tles/{group}.tle."
    )


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
