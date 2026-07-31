"""Vercel serverless function: hands the frontend its data + token config.

On load the frontend fetches ``/api/config`` and, when ``data_url`` is set,
pulls the merged ``positions.json`` straight from the public GCS bucket (not
the repo's committed data files). This is the only backend piece deployed to
Vercel — the FastAPI app in ``api/main.py`` is for local dev only and is
excluded from the Vercel build via ``.vercelignore``.

Intentionally stdlib-only (no project imports) so the function stays tiny and
does not pull the pipeline's heavy deps into its request path.
"""
import json
import os
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(
            {
                # Public https URL of positions.json in the GCS data bucket.
                # Empty in local dev -> frontend falls back to /api/orbits + /api/events.
                "data_url": os.environ.get("DATA_URL", ""),
                # Optional Cesium Ion token for terrain/imagery; empty -> free defaults.
                "cesium_ion_token": os.environ.get("CESIUM_ION_TOKEN", ""),
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        # The URL/token can change between deploys; never let the CDN cache it.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)