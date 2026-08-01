"""Cloud Function entry point (serverless backend).

Runs the satellite pipeline (fetch -> propagate -> close-approach detection)
against a writable temp dir, then publishes a compact binary container
(``positions.bin``) to a public GCS bucket the Cesium frontend fetches
directly. Triggered by Cloud Scheduler on an HTTP schedule.

The container moves the large ECEF sample array out of JSON into a raw
little-endian int32 blob so the browser can ``fetch().arrayBuffer()`` and view
it as an ``Int32Array`` with zero parsing, while a small JSON header carries
metadata, the time grid, the satellite list, and the (already ECEF-embedded)
close-approach events.

This file is the serverless entry point only; running it locally as a script
does nothing useful. The pipeline and ``config`` are reused from the repo
root, so this thin wrapper stays in sync with the local dev path.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import struct

# The pipeline writes outputs under ``config.DATA_DIR``, which defaults to
# ./data. Cloud Functions only allow writes under /tmp, so override this
# BEFORE any config/pipeline import — config reads the env at module load.
os.environ.setdefault("DATA_DIR", "/tmp/data")

import functions_framework  # noqa: E402
import numpy as np  # noqa: E402
from google.cloud import storage  # noqa: E402

from config import EVENTS_FILE, ORBITS_FILE  # noqa: E402
from pipeline.run_pipeline import run  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("function")

DATA_BUCKET = os.environ.get("DATA_BUCKET", "")

# Binary container format tag (so the frontend can sanity-check / version it).
_MAGIC = b"STB1"


def _build_container(orbits: dict, events: dict) -> bytes:
    """Pack the merged payload into the STB1 binary container.

    Layout (little-endian)::

        b"STB1"                      4-byte magic
        uint32 header_json_len        byte length of the UTF-8 header JSON
        header_json bytes             padded to 4-byte alignment
        int32[n * t * 3]             flat ECEF meters, sat-major, step-major, xyz

    The header carries everything except the big ECEF array: metadata, the time
    grid, the satellite list (with per-sat sample counts to handle sats that
    dropped timesteps during propagation), and the close-approach events (each
    already carrying its own ``ecef_a``/``ecef_b``). The ECEF blob is one row
    per satellite in the same order as the header's ``orbits`` list, each row
    padded with zeros to the full grid stride.
    """
    n_t = int(orbits["n_timesteps"])
    stride = n_t * 3  # int32 values per satellite row

    orbit_meta = []
    rows = []
    for o in orbits["orbits"]:
        e = np.array(o["ecef_m"], dtype="<i4")
        n_steps = e.size // 3
        orbit_meta.append({"sat_id": o["sat_id"], "name": o["name"], "n_steps": n_steps})
        # Pad partial satellites (propagation dropouts) to the full grid stride
        # so every row is uniform and the frontend can index by sat*stride.
        if e.size < stride:
            e = np.concatenate([e, np.zeros(stride - e.size, dtype="<i4")])
        elif e.size > stride:
            e = e[:stride]
        rows.append(e)

    header = {
        "groups": orbits.get("groups"),
        "group": orbits.get("group"),
        "fetch_time": orbits.get("fetch_time"),
        "n_satellites": len(orbit_meta),
        "n_timesteps": n_t,
        "t": orbits.get("t", []),
        "orbits": orbit_meta,
        "events": events.get("events", []),
    }
    hdr_json = json.dumps(header, separators=(",", ":")).encode("utf-8")

    prefix = _MAGIC + struct.pack("<I", len(hdr_json)) + hdr_json
    prefix += b"\x00" * ((-len(prefix)) % 4)  # align the int32 blob to 4 bytes

    ecef_blob = np.concatenate(rows).tobytes() if rows else b""
    return prefix + ecef_blob


@functions_framework.http
def refresh(request):
    """Run the pipeline once and publish positions.bin to the data bucket.

    Returns a small JSON summary of the run. Intended to be invoked by Cloud
    Scheduler with an OIDC identity token, but also callable by hand for tests.
    """
    if not DATA_BUCKET:
        raise RuntimeError("DATA_BUCKET env var is required.")

    # The pipeline writes orbits.json + events.json under DATA_DIR (/tmp/data).
    summary = run()
    log.info("Pipeline summary: %s", summary)

    orbits = json.loads(ORBITS_FILE.read_text(encoding="utf-8"))
    events = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
    container = _build_container(orbits, events)

    # Upload pre-compressed bytes with Content-Encoding: gzip. GCS serves them
    # gzip to browsers (all send Accept-Encoding: gzip), which decompress
    # transparently — fetch().arrayBuffer() returns the binary container. The
    # raw int32 ECEF blob needs no JSON parsing on the client, which is the
    # point: it cuts the dominant parse cost and shrinks the wire payload.
    gz_bytes = gzip.compress(container, compresslevel=9)
    # Data refreshes every 12h. A 1h max-age lets reloads reuse the cached blob;
    # revalidation afterward returns 304 while the object is unchanged, and a
    # new object generation (each refresh) changes the ETag so updates are seen.
    client = storage.Client()
    blob = client.bucket(DATA_BUCKET).blob("positions.bin")
    blob.content_encoding = "gzip"
    blob.cache_control = "public, max-age=3600"
    blob.upload_from_string(gz_bytes, content_type="application/octet-stream")
    log.info(
        "Uploaded positions.bin -> gs://%s/positions.bin (%.1f MB raw -> %.1f MB gz, %d sats, %d steps)",
        DATA_BUCKET,
        len(container) / 1e6,
        len(gz_bytes) / 1e6,
        int(orbits.get("n_satellites", 0)),
        int(orbits.get("n_timesteps", 0)),
    )
    return {"ok": True, **summary}