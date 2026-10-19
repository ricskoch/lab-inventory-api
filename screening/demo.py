"""One command demo: API + simulated screening week + dashboard.

  python -m screening.demo            (Windows: py -m screening.demo)

Creates a throwaway database and fresh keys in demo_data/, starts the API and the dashboard, runs a
simulated week, and prints the dashboard address. Press Ctrl+C to stop everything.
Needs: pip install -r requirements.txt -r requirements-dashboard.txt
"""
import argparse
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from app.db import init_db
from app.security import create_key


def free_port(preferred: int) -> int:
    for port in (preferred, *range(preferred + 1, preferred + 50)):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:  # nobody is listening there
                return port
    raise SystemExit(f"no free port near {preferred}")


def wait_for_port(port: int, what: str, timeout: float = 40):
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.3)
    raise SystemExit(f"{what} did not start on port {port}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plates", type=int, default=4)
    ap.add_argument("--day-seconds", type=float, default=6.0, help="real seconds per simulated day")
    ap.add_argument("--jam-rate", type=float, default=0.1)
    a = ap.parse_args()

    try:
        import streamlit  # noqa: F401
    except ImportError:
        raise SystemExit("Streamlit is missing. Run:  python -m pip install -r requirements-dashboard.txt")

    work = Path("demo_data")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    os.environ["LAB_DB"] = str(work / "demo.db")
    init_db()
    conn = sqlite3.connect(os.environ["LAB_DB"])
    keys = {role: create_key(conn, f"demo-{role}", role) for role in ("admin", "device", "reader")}
    conn.close()

    api_port, dash_port = free_port(8000), free_port(8501)
    url = f"http://127.0.0.1:{api_port}"
    env = {**os.environ, "LAB_API_URL": url, "LAB_ADMIN_KEY": keys["admin"], "LAB_DEVICE_KEY": keys["device"],
           "LAB_API_KEY": keys["reader"]}
    py = sys.executable
    procs = []
    try:
        print(f"Starting the API on {url} ...")
        procs.append(subprocess.Popen([py, "-m", "uvicorn", "app.main:app", "--port", str(api_port), "--log-level", "warning"], env=env))
        wait_for_port(api_port, "the API")
        procs.append(subprocess.Popen([py, "-m", "streamlit", "run", "dashboard/app.py", "--server.port", str(dash_port),
                                       "--server.headless", "true", "--browser.gatherUsageStats", "false"],
                                      env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_for_port(dash_port, "the dashboard")
        print(f"\n>>> Dashboard: http://localhost:{dash_port}   (open it now; tick 'Live view' in the sidebar)")
        print(">>> In Codespaces, open the Ports tab and click the globe next to that port.\n")
        subprocess.run([py, "-m", "screening.run_week", "--plates", str(a.plates), "--day-seconds", str(a.day_seconds),
                        "--jam-rate", str(a.jam_rate), "--work", str(work / "run")], env=env, check=True)
        print(f"\nThe week is done. The dashboard stays up at http://localhost:{dash_port}. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping ...")
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
