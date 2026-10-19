"""The controller PC: runs the weekly screening schedule by sending commands to the lab's devices.

Week (day 0 = Monday): seed -> baseline image + drug addition -> daily imaging -> final image + plate reader.
Every step is also logged to the API as a workflow event, so the dashboard can show what happened when.
"""
import socket
import time
from dataclasses import dataclass
from datetime import timedelta

import httpx

from .lab_sim import SimClock, stamp

PORT_OFFSET = {"arm": 0, "dispenser": 1, "microscope": 2, "reader": 3}
DEVICE_NAME = {"arm": "robot-arm-1", "dispenser": "dispenser-1", "microscope": "microscope-1", "reader": "plate-reader-1"}
HOUR = 3600


@dataclass(frozen=True)
class Step:
    at: int            # simulated seconds after Monday 08:00
    plate: str
    device: str        # arm | dispenser | microscope | reader
    command: str
    kind: str          # move | seed | add_drug | image | read
    day: int


def steps_for_day(day: int, plate: str) -> list[tuple[str, str, str]]:
    """(device, command, kind) in order for one plate on one day."""
    move = lambda a, b: ("arm", f"MOVE {plate} {a} {b}", "move")
    if day == 0:
        return [move("STOCK", "DISPENSER"), ("dispenser", f"SEED {plate}", "seed"), move("DISPENSER", "INCUBATOR")]
    seq = [move("INCUBATOR", "IMAGER"), ("microscope", f"IMAGE {plate} {day}", "image")]
    if day == 1:   # baseline image first, then the drugs go on
        return seq + [move("IMAGER", "DISPENSER"), ("dispenser", f"ADD_DRUG {plate}", "add_drug"), move("DISPENSER", "INCUBATOR")]
    if day == 4:   # last day: image, then the destructive plate-reader endpoint
        return seq + [move("IMAGER", "READER"), ("reader", f"READ {plate} {day}", "read"), move("READER", "WASTE")]
    return seq + [move("IMAGER", "INCUBATOR")]


def build_schedule(plates: list[str], days: int = 5) -> list[Step]:
    """Times are simulated seconds after Monday 08:00. Plates are handled one after another, 30 simulated
    minutes apart, 5 minutes per step, starting at 08:00 each day."""
    out = []
    for day in range(days):
        for i, plate in enumerate(plates):
            t = day * 24 * HOUR + i * 30 * 60
            for device, command, kind in steps_for_day(day, plate):
                out.append(Step(t, plate, device, command, kind, day))
                t += 5 * 60
    return sorted(out, key=lambda s: s.at)


class LabError(Exception):
    pass


class LabLink:
    """One TCP connection per device; retries transient errors such as a jammed gripper."""

    def __init__(self, host: str, base_port: int):
        self.socks = {d: socket.create_connection((host, base_port + off), timeout=10) for d, off in PORT_OFFSET.items()}
        self.files = {d: s.makefile("rw", newline="\n") for d, s in self.socks.items()}

    def send(self, device: str, command: str, retries: int = 3) -> str:
        for attempt in range(retries + 1):
            f = self.files[device]
            f.write(command + "\n"); f.flush()
            reply = f.readline().strip()
            if reply.startswith("OK"):
                return reply
            if "arm_jam" in reply and attempt < retries:
                time.sleep(0.05)
                continue
            raise LabError(f"{DEVICE_NAME[device]} said: {reply}  (command: {command})")
        raise LabError("unreachable")

    def close(self):
        for s in self.socks.values():
            s.close()


def run(steps: list[Step], link: LabLink, clock: SimClock, api: httpx.Client | None, run_id: str, log=print) -> dict:
    """Execute the schedule in (compressed) real time. Returns simple counters."""
    device_ids = {}
    if api:
        device_ids = {d["name"]: d["id"] for d in api.get("/devices").json()}
    stats = {"steps": 0}
    for n, step in enumerate(steps):
        wait = clock.real_time_of(step.at) - time.time()
        if wait > 0:
            time.sleep(wait)
        reply = link.send(step.device, step.command)
        stats["steps"] += 1
        now = clock.now()
        log(f"{stamp(now)}  day {step.day}  {step.plate:8} {DEVICE_NAME[step.device]:14} {step.command:32} -> {reply[:40]}")
        if api:
            r = api.post("/events", json={
                "kind": step.kind, "plate_barcode": step.plate, "device_id": device_ids.get(DEVICE_NAME[step.device]),
                "detail": {"command": step.command, "reply": reply, "day": step.day},
                "ts": stamp(now), "external_id": f"controller:{run_id}:{n}",
            })
            r.raise_for_status()
    return stats
