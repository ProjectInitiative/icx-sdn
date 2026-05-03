import http.server
import json
import os
import subprocess
import sys
import webbrowser
import threading
from pathlib import Path

from . import _project_root

PROJECT_ROOT = _project_root()
DATA_DIR = PROJECT_ROOT / "data"
DATA_FILE = DATA_DIR / "latest.json"
LIVE_FILE = DATA_DIR / "live.json"
STATIC_DIR = PROJECT_ROOT / "static"
COMMUNITY_FILE = DATA_DIR / "snmp_community.txt"
PORT = int(os.environ.get("ICX_SERVER_PORT", "8080"))


class SwitchHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self):
        if self.path == "/api/data":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if DATA_FILE.exists():
                with open(DATA_FILE) as f:
                    self.wfile.write(f.read().encode())
            else:
                self.wfile.write(
                    json.dumps({"error": "No data — run ingest first"}).encode()
                )
            return

        if self.path == "/api/live":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if LIVE_FILE.exists():
                with open(LIVE_FILE) as f:
                    self.wfile.write(f.read().encode())
            else:
                self.wfile.write(
                    json.dumps(
                        {"error": "No live data — configure SNMP and start poller"}
                    ).encode()
                )
            return

        if self.path == "/api/ingest":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "icx_monitor.ingest"],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                out = {
                    "success": result.returncode == 0,
                    "output": result.stdout,
                    "error": result.stderr,
                }
            except subprocess.TimeoutExpired:
                out = {"success": False, "error": "Ingest timed out"}
            except Exception as e:
                out = {"success": False, "error": str(e)}
            self.wfile.write(json.dumps(out).encode())
            return

        return super().do_GET()

    def log_message(self, format, *args):
        if "/favicon.ico" in str(args):
            return
        msg = format % args if args else format
        print(f"[{self.address_string()}] {msg}")


def start_snmp_poller():
    if not COMMUNITY_FILE.exists():
        return
    import shutil

    env = os.environ.copy()
    env.setdefault("PATH", "")
    for tool in ["snmpwalk", "snmpget", "nix-shell"]:
        path = shutil.which(tool)
        if path:
            env["PATH"] = str(Path(path).parent) + ":" + env["PATH"]
    proc = subprocess.Popen(
        [sys.executable, "-m", "icx_monitor.live", "--watch"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=env,
    )
    print(f"  SNMP poller started (PID {proc.pid})")
    import threading

    def log_stderr():
        for line in iter(proc.stderr.readline, b""):
            print(f"  [snmp-poller] {line.decode().strip()}")

    threading.Thread(target=log_stderr, daemon=True).start()


def serve():
    os.makedirs(STATIC_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    server = http.server.HTTPServer(("0.0.0.0", PORT), SwitchHandler)
    print(f"Serving at http://0.0.0.0:{PORT}")
    print(f"  API: http://localhost:{PORT}/api/data")
    print(f"  API: http://localhost:{PORT}/api/live")
    print(f"  API: http://localhost:{PORT}/api/ingest")
    print(f"  UI:  http://localhost:{PORT}/")

    if COMMUNITY_FILE.exists():
        start_snmp_poller()
    else:
        print(f"  SNMP: create {COMMUNITY_FILE} and restart for live polling")

    threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


def main():
    serve()


if __name__ == "__main__":
    main()
