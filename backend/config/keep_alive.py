"""Standalone keep-alive pinger.

Sends a GET request to https://findayintelligence.com every 12 minutes
(useful to stop a free-tier host from sleeping). Stdlib only.

Standalone:  python backend/config/keep_alive.py   (Ctrl+C to stop)
With Django: config/wsgi.py calls start() so it runs under runserver and
gunicorn. To unwire, remove (or comment out) that one line in wsgi.py.
"""
import threading
import time
import urllib.request
from datetime import datetime

URL = "https://findayintelligence.com"
INTERVAL_SECONDS = 10 * 60
TIMEOUT_SECONDS = 30


def ping():
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "findayintelligence-keepalive/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            print(f"[{stamp}] {URL} -> {resp.status}", flush=True)
    except Exception as exc:  # keep looping on any failure
        print(f"[{stamp}] {URL} -> FAILED: {exc}", flush=True)


def run_forever():
    while True:
        ping()
        time.sleep(INTERVAL_SECONDS)


def start():
    """Start the pinger in a daemon thread (no-op if already started)."""
    if any(t.name == "keep-alive" for t in threading.enumerate()):
        return
    threading.Thread(target=run_forever, name="keep-alive", daemon=True).start()


if __name__ == "__main__":
    print(f"Pinging {URL} every {INTERVAL_SECONDS // 60} minutes. Ctrl+C to stop.", flush=True)
    run_forever()
