"""
dashboard.py - a tiny live web dashboard for parking.db.

Runs a small local web server using ONLY Python's built-in http.server
module - no new dependencies (no Flask/FastAPI). It serves:
  - "/"           the dashboard page (dashboard.html)
  - "/api/spots"  the current spot data, as JSON

dashboard.html (loaded in your browser) polls "/api/spots" once a
second and redraws a grid of colored boxes, so you can watch spots
flip live without touching a terminal.

This script does NOT talk to MQTT at all - it only reads parking.db,
the same file subscriber.py is writing to. Run subscriber.py and
simulator.py in their own terminals as usual, then run this in a
third terminal and open http://localhost:8000 in your browser.
"""

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer

DB_FILE = "parking.db"
HOST = "localhost"
PORT = 8000


def get_spots_data() -> dict:
    """Read the current state of the world from parking.db and shape it
    into something easy for the browser to turn into a grid.

    If parking.db doesn't exist yet, or exists but subscriber.py hasn't
    created its tables yet (it hasn't received a first message), we
    return an empty result instead of crashing - the dashboard just
    shows "no spots yet" until subscriber.py catches up.
    """
    conn = sqlite3.connect(DB_FILE)

    try:
        rows = conn.execute(
            "SELECT spot, garage, floor, status, updated_at "
            "FROM spot_status ORDER BY spot"
        ).fetchall()

        (event_count,) = conn.execute(
            "SELECT COUNT(*) FROM occupancy_events"
        ).fetchone()
    except sqlite3.OperationalError:
        # Tables don't exist yet - subscriber.py creates them on its
        # first message. Nothing to show yet, that's fine.
        rows = []
        event_count = 0
    finally:
        conn.close()

    spots = [
        {
            "spot": spot,
            "garage": garage,
            "floor": floor,
            "status": status,
            "updated_at": updated_at,
        }
        for spot, garage, floor, status, updated_at in rows
    ]
    free_count = sum(1 for s in spots if s["status"] == "free")

    return {
        "spots": spots,
        "free_count": free_count,
        "total": len(spots),
        "event_count": event_count,
    }


class DashboardHandler(BaseHTTPRequestHandler):
    """Handles every HTTP request that comes in to our tiny server.

    http.server calls do_GET() for us on every GET request - we just
    look at the requested path and decide what to send back.
    """

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._serve_file("dashboard.html", "text/html")
        elif self.path == "/api/spots":
            self._serve_json(get_spots_data())
        else:
            self.send_error(404, "Not found")

    def _serve_file(self, filename: str, content_type: str):
        try:
            with open(filename, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            self.send_error(404, f"{filename} not found next to dashboard.py")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # Without this, some browsers cache this response and the page
        # ends up showing the same stale snapshot forever instead of
        # re-reading parking.db on every poll.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # The default behavior prints every single request to the
        # terminal - since the browser polls once a second, that gets
        # noisy fast. We silence it here.
        pass


def main():
    server = HTTPServer((HOST, PORT), DashboardHandler)
    print(f"Dashboard running at http://{HOST}:{PORT}")
    print("Open that URL in your browser. Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard...")
        server.server_close()


if __name__ == "__main__":
    main()
