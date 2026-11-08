"""Vercel serverless function: GET /api/search?from=JFK&to=LIS&start=2027-01-10&days=14&nights=7"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from airline_searcher.web import query_from_url, search_api  # noqa: E402


class handler(BaseHTTPRequestHandler):  # Vercel looks for a class named "handler"
    def do_GET(self):
        status, body = search_api(query_from_url(self.path))
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)
