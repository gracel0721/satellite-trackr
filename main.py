"""Cloud Function entry point (serverless backend).

Runs the satellite pipeline (fetch -> propagate -> close-approach detection)
against a writable temp dir, merges the produced orbits + events into a single
``positions.json``, and uploads it to a public GCS bucket the Cesium frontend
fetches directly. Triggered by Cloud Scheduler on an HTTP schedule.

This file is the serverless entry point only; running it locally as a script
does nothing useful. The pipeline and ``config`` are reused from the repo
root, so this thin wrapper stays in sync with the local dev path.
"""
from __future__ import annotations

import json
import logging
import os

# The pipeline writes outputs under ``config.DATA_DIR``, which defaults to
# ./data. Cloud Functions only allow writes under /tmp, so override this
# BEFORE any config/pipeline import — config reads the env at module load.
os.environ.setdefault("DATA_DIR", "/tmp/data")

import functions_framework  # noqa: E402
from google.cloud import storage  # noqa: E402

from config import EVENTS_FILE, ORBITS_FILE  # noqa: E402
from pipeline.run_pipeline import run  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("function")

DATA_BUCKET = os.environ.get("DATA_BUCKET", "")


@functions_framework.http
def refresh(request):
    """Run the pipeline once and publish positions.json to the data bucket.

    Returns a small JSON summary of the run. Intended to be invoked by Cloud
    Scheduler with an OIDC identity token, but also callable by hand for tests.
    """
    if not DATA_BUCKET:
        raise RuntimeError("DATA_BUCKET env var is required.")

    # The pipeline writes orbits.json + events.json under DATA_DIR (/tmp/data).
    summary = run()
    log.info("Pipeline summary: %s", summary)

    # Merge the two payloads into the single file the frontend fetches. The
    # orbits payload already carries the time grid, satellite list, and ECEF
    # samples; events carry the flagged close approaches against that grid.
    orbits = json.loads(ORBITS_FILE.read_text(encoding="utf-8"))
    events = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
    payload = {**orbits, "events": events.get("events", [])}

    client = storage.Client()
    blob = client.bucket(DATA_BUCKET).blob("positions.json")
    blob.upload_from_string(
        json.dumps(payload, separators=(",", ":")),
        content_type="application/json",
    )
    log.info("Uploaded positions.json -> gs://%s/positions.json", DATA_BUCKET)
    return {"ok": True, **summary}