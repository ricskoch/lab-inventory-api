"""The simulated lab: a robot arm, a dispenser, a microscope and a plate reader, each with its own TCP port.

Like real instruments they speak a simple text protocol, one command per line:
    robot arm    9201   MOVE <plate> <FROM> <TO>
    dispenser    9202   SEED <plate> | ADD_DRUG <plate>
    microscope   9203   IMAGE <plate> <day>        (writes a CSV file into <out>/microscope/)
    plate reader 9204   READ <plate> <day>         (writes a CSV file into <out>/reader/)
Answers are `OK ...` or `ERR ...`. The world enforces physics: a plate can only be imaged while it is
on the imager, a station holds one plate, a plate cannot be seeded twice.

Run it: python -m screening.lab_sim --plates S1-P1 S1-P2 --out run --t0 <epoch> --sim-start 2026-10-19T08:00:00Z
"""
import argparse
import csv
import random
import socketserver
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .biology import PlateState

STATIONS = ("STOCK", "INCUBATOR", "DISPENSER", "IMAGER", "READER", "WASTE")
SINGLE_SLOT = ("DISPENSER", "IMAGER", "READER")  # these hold one plate at a time


class SimClock:
    """Maps real time to simulated lab time: one simulated day lasts `day_seconds` real seconds."""

    def __init__(self, sim_start: datetime, t0_real: float, day_seconds: float):
        self.sim_start, self.t0, self.factor = sim_start, t0_real, 86400 / day_seconds

    def now(self) -> datetime:
        return self.sim_start + timedelta(seconds=(time.time() - self.t0) * self.factor)

    def real_time_of(self, sim_seconds: float) -> float:
        return self.t0 + sim_seconds / self.factor


def stamp(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class World:
    def __init__(self, plates: list[str], clock: SimClock, out: Path, seed: int = 1,
                 jam_rate: float = 0.0, noise: bool = True):
        self.clock, self.out, self.jam_rate = clock, Path(out), jam_rate
        self.rng = random.Random(seed)
        self.lock = threading.Lock()
        self.state = {bc: PlateState(bc, i + 1, seed, noise) for i, bc in enumerate(plates)}
        self.where = {bc: "STOCK" for bc in plates}
        for sub in ("microscope", "reader"):
            (self.out / sub).mkdir(parents=True, exist_ok=True)

    # --- helpers ---
    def _plate(self, name: str) -> str:
        if name not in self.state:
            raise LabError(f"unknown plate {name}")
        return name

    def _require_at(self, plate: str, station: str):
        if self.where[plate] != station:
            raise LabError(f"plate {plate} is at {self.where[plate]}, not {station}")

    def _write(self, sub: str, plate: str, day: int, metric: str, values: dict[str, float], t: datetime) -> str:
        name = f"{plate}__day{day}__{metric}.csv"
        tmp = self.out / sub / f".{name}.tmp"
        with tmp.open("w", newline="") as f:  # hidden temp name, then rename: readers never see half a file
            w = csv.writer(f)
            w.writerow(["well", "value", "measured_at"])
            for well in sorted(values):
                w.writerow([well, values[well], stamp(t)])
        tmp.rename(self.out / sub / name)
        return name

    # --- device commands ---
    def arm(self, cmd: list[str]) -> str:
        if cmd[0] == "PING":
            return "OK arm ready"
        if cmd[0] != "MOVE" or len(cmd) != 4:
            raise LabError("usage: MOVE <plate> <FROM> <TO>")
        plate, src, dst = self._plate(cmd[1]), cmd[2].upper(), cmd[3].upper()
        if src not in STATIONS or dst not in STATIONS:
            raise LabError(f"unknown station; use one of {', '.join(STATIONS)}")
        with self.lock:
            if self.rng.random() < self.jam_rate:
                raise LabError("arm_jam gripper did not close, retry")
            self._require_at(plate, src)
            if dst in SINGLE_SLOT and dst in self.where.values():
                raise LabError(f"{dst} is occupied")
            self.where[plate] = dst
        return f"OK moved {plate} {src}->{dst}"

    def dispenser(self, cmd: list[str]) -> str:
        if cmd[0] == "PING":
            return "OK dispenser ready"
        if len(cmd) != 2 or cmd[0] not in ("SEED", "ADD_DRUG"):
            raise LabError("usage: SEED <plate> | ADD_DRUG <plate>")
        plate = self._plate(cmd[1])
        with self.lock:
            self._require_at(plate, "DISPENSER")
            st, now = self.state[plate], self.clock.now()
            if cmd[0] == "SEED":
                if st.seeded_at:
                    raise LabError(f"plate {plate} is already seeded")
                st.seed(now)
                return f"OK seeded {plate} with 2000 cells per well"
            if not st.seeded_at:
                raise LabError(f"plate {plate} has no cells yet")
            if st.dosed_at:
                raise LabError(f"plate {plate} already has drug")
            st.add_drug(now)
            return f"OK added drug library to {plate}"

    def microscope(self, cmd: list[str]) -> str:
        if cmd[0] == "PING":
            return "OK microscope ready"
        if len(cmd) != 3 or cmd[0] != "IMAGE":
            raise LabError("usage: IMAGE <plate> <day>")
        plate = self._plate(cmd[1])
        with self.lock:
            self._require_at(plate, "IMAGER")
            now = self.clock.now()
            name = self._write("microscope", plate, int(cmd[2]), "confluence_pct", self.state[plate].confluence(now), now)
        return f"OK file={name}"

    def reader(self, cmd: list[str]) -> str:
        if cmd[0] == "PING":
            return "OK reader ready"
        if len(cmd) != 3 or cmd[0] != "READ":
            raise LabError("usage: READ <plate> <day>")
        plate = self._plate(cmd[1])
        with self.lock:
            self._require_at(plate, "READER")
            now = self.clock.now()
            name = self._write("reader", plate, int(cmd[2]), "luminescence", self.state[plate].luminescence(now), now)
        return f"OK file={name}"


class LabError(Exception):
    pass


def make_handler(world: World, device: str):
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            for raw in self.rfile:
                parts = raw.decode().split()
                if not parts:
                    continue
                try:
                    reply = getattr(world, device)([parts[0].upper(), *parts[1:]])
                except (LabError, ValueError, KeyError) as e:
                    reply = f"ERR {e}"
                self.wfile.write((reply + "\n").encode())

    return Handler


def serve(world: World, base_port: int):
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    servers = []
    for i, device in enumerate(("arm", "dispenser", "microscope", "reader")):
        srv = Server(("127.0.0.1", base_port + i), make_handler(world, device))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
    return servers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plates", nargs="+", required=True)
    ap.add_argument("--out", default="screening_run")
    ap.add_argument("--base-port", type=int, default=9201)
    ap.add_argument("--t0", type=float, default=None, help="real start time (epoch seconds), shared with the controller")
    ap.add_argument("--sim-start", default="2026-10-19T08:00:00Z")
    ap.add_argument("--day-seconds", type=float, default=30.0, help="real seconds per simulated day")
    ap.add_argument("--jam-rate", type=float, default=0.0, help="chance a robot move fails and must be retried")
    a = ap.parse_args()
    clock = SimClock(datetime.fromisoformat(a.sim_start.replace("Z", "+00:00")), a.t0 or time.time(), a.day_seconds)
    world = World(a.plates, clock, Path(a.out), jam_rate=a.jam_rate)
    serve(world, a.base_port)
    print(f"lab simulator up on ports {a.base_port}-{a.base_port + 3}, {len(a.plates)} plates at STOCK", flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    main()
