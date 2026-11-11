"""Vercel serverless function: GET /api/health -> whether live search is configured."""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from airline_searcher.web import health, query_from_url  # noqa: E402


class handler(BaseHTTPRequestHandler):  # Vercel looks for a class named "handler"
    def do_GET(self):
        payload = json.dumps(health(query_from_url(self.path).get("check") == "1")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)
