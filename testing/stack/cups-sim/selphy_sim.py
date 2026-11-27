#!/usr/bin/env python3
"""Simulated Canon SELPHY CP1300 for the virtual test setup (FSD §17.1).

One daemon holds the printer model. Everything that would talk to the real
printer talks to the daemon instead:

  * the CUPS backend `sim-selphy` (one process per job, started by CUPS), which
    writes the same stderr lines as Gutenprint's `canonselphyneo` backend and
    exits with the same status codes;
  * the printer status query (`BACKEND=canonselphyneo gutenprint53+usb -s`),
    answered with the same lines the real query printed in P-1/B-1;
  * the tests, through a small HTTP control API (scenario, physical actions such
    as refilling paper or a power cycle, logs, CUPS helpers).

The printer is modelled physically, so held jobs and duplicates arise the way
they do on the real printer: the printer keeps a job it has received, prints it
by itself once a paper or ink error is fixed, loses it on a power cycle, and
prints it again if CUPS resends it (R3, R18, A4).

Message texts, ordering and timings come from the bench logs: P-1 job 118/119,
B-1 jobs 125-129 (FSD §17.3.1, §17.3.2). Anything not observed on the real
printer is marked "(assumed)".

Usage:
  selphy_sim.py daemon                 run the daemon (control API + backend port)
  selphy_sim.py backend [CUPS args]    CUPS backend (installed as sim-selphy)
  selphy_sim.py query                  one printer status query (as `-s`)
"""

import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    import yaml
except ImportError:  # the backend and query clients do not need it
    yaml = None

CONTROL_PORT = int(os.environ.get("SIM_CONTROL_PORT", "8631"))
BACKEND_PORT = int(os.environ.get("SIM_BACKEND_PORT", "8632"))
DAEMON_HOST = os.environ.get("SIM_DAEMON_HOST", "127.0.0.1")
QUEUE = "selphy"
DEVICE_URI = "sim-selphy://canon-cp1300/SIM0001"

# CUPS backend exit codes (cups/backend.h).
CUPS_BACKEND_OK = 0
CUPS_BACKEND_FAILED = 1
CUPS_BACKEND_STOP = 4
CUPS_BACKEND_CANCEL = 5

# Printer error codes and texts of Gutenprint 5.3.4 as installed on the booth
# laptop (Debian 12, backend_canonselphyneo 0.22). The query prints the text
# without the code ("Printer error: Paper Feed"), the CUPS backend with it
# ("Printer error: Paper Feed (02)").
ERRORS = {
    "02": "Paper Feed",
    "03": "No Paper",
    "05": "Incorrect Paper loaded",
    "06": "Ink Cassette Empty",
    "07": "No Ink",
    "08": "Unknown Error",
    "09": "No Paper and Ink",
    "0a": "Incorrect media for job",
    "0b": "Paper jam",
    "0c": "Unknown Error",
}
# Errors the printer recovers from by itself once the cause is fixed: it prints
# the held photo without a button press (P-1/B-1, A4). The others need a power
# cycle, which loses the held photo (0c seen in P-1; 0b from the owner, A4).
SELF_RESUMING = {"02", "03", "05", "06", "07", "08", "09"}
# Errors that set printer-state-reasons media-empty (P-1: paper and ink errors).
MEDIA_EMPTY = {"02", "03", "06", "07", "09"}

LAYERS = ["YELLOW", "MAGENTA", "CYAN", "LAMINATE"]

# Timeline of one print in simulated seconds after the printer received the
# job. B-1 jobs 125/128/129: Feeding 1, YELLOW 9-10, MAGENTA 21, CYAN 33,
# LAMINATE 44, eject ~55.5, Print complete 56.
DEFAULT_TIMING = {
    "feeding": 1.0,
    "layers": [10.0, 21.0, 33.0, 44.0],
    "eject": 55.5,
    "complete": 56.0,
    # Error delays after the printer starts feeding (P-1/B-1): empty tray
    # Paper Feed (02) ~13-14 s, missing tray No Paper (03) ~4 s, missing ink
    # No Ink (07) ~4 s. Incorrect media (0a) (assumed) 2 s.
    "error_after": {"02": 13.0, "03": 4.0, "07": 3.0, "09": 3.0, "05": 3.0, "08": 3.0, "0a": 2.0, "06": 3.0},
    # After a fix the printer starts the held print by itself: first pass
    # 16 s (paper) / 8 s (ink) after the fix in B-1; the pass comes ~2 s
    # after feeding starts, so feeding restarts after this delay.
    "resume_after": {"02": 8.0, "03": 8.0, "default": 6.0},
    # Backend reports the printer state about once a second (FAST_RETURN=0).
    "report_interval": 1.0,
    # Backend start until "Sending spool data" (filters, USB open).
    "spool": 1.0,
}

QUERY_PREAMBLE = [
    "STATE: +connecting-to-device",
    "STATE: +org.gutenprint.searching-for-device",
    "DEBUG: Probing VID: 04A9 PID: 32db",
    "STATE: -org.gutenprint.searching-for-device",
    "STATE: -offline-report",
    "STATE: -connecting-to-device",
    "DEBUG: Initializing 'Canon SELPHY CP (new)' backend (version 0.22)",
]
QUERY_PPD = [
    'PPD: StpUsbBackend="canonselphyneo"',
    "PPD: StpUsbVid=04a9",
    "PPD: StpUsbPid=32db",
    "PPD: StpUsbBus=001",
    "PPD: StpUsbPort=001",
]
QUERY_HANG_LINES = [
    "ERROR: Failure to receive data from printer (libusb error -7: (0/12 from 0x82))",
    "ERROR: Unable to attach to printer!",
    "STATE: -connecting-to-device",
]


def now():
    return time.time()


def iso(t):
    return time.strftime("%H:%M:%S", time.localtime(t)) + ".%03d" % int((t % 1) * 1000)


# ---------------------------------------------------------------------------
# Printer model
# ---------------------------------------------------------------------------


class Printer:
    """The physical printer. All times are simulated seconds × scale."""

    def __init__(self):
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.reset({})

    # -- scenario -----------------------------------------------------------

    def reset(self, scenario):
        with self.lock:
            self.scenario = scenario or {}
            clock = self.scenario.get("clock", {})
            self.scale = float(clock.get("scale", os.environ.get("SIM_TIME_SCALE", "1")))
            timing = json.loads(json.dumps(DEFAULT_TIMING))
            for key, value in (self.scenario.get("timing") or {}).items():
                if isinstance(value, dict):
                    timing[key].update(value)
                else:
                    timing[key] = value
            self.timing = timing
            phys = self.scenario.get("printer") or {}
            self.tray = phys.get("tray", "ok")  # ok | empty | missing
            self.ink = phys.get("ink", "ok")  # ok | missing | empty | wrong
            self.paper_sheets = phys.get("paper_sheets")  # None = unlimited
            self.ink_prints = phys.get("ink_prints")
            self.wrong_media = bool(phys.get("wrong_media", False))
            self.connected = bool(phys.get("connected", True))
            self.self_print = bool(phys.get("self_print", True))
            self.job_specs = list(self.scenario.get("jobs") or [])
            q = self.scenario.get("query") or {}
            self.query_forced = list(q.get("answers") or [])
            self.query_hang_on_layer_change = bool(q.get("hang_on_layer_change", False))
            self.query_hang_s = float(q.get("hang_s", 17.0))
            self.query_wrong_layer_after_hang = bool(q.get("wrong_layer_after_hang", False))
            self.query_exit_on_hang = int(q.get("exit_on_hang", CUPS_BACKEND_FAILED))
            self.state = "idle"  # idle | feeding | printing | error
            self.layer = None
            self.error = None  # code
            self.error_state_text = None  # printer state line shown with the error
            self.held = None  # job dict the printer holds
            self.generation = 0  # bumps on power cycle / unplug: aborts runs
            self.run_thread = None
            self.last_layer_change = 0.0
            self.hang_pending_wrong_layer = False
            self.received = []  # every job the printer received (incl. resends)
            self.outputs = []  # every photo that came out
            self.queries = []
            self.violations = []
            self.actions = []
            self.backend_runs = []
            self.events = []
            self.loaded_at = now()
            self.cond.notify_all()
        for ev in self.scenario.get("events") or []:
            threading.Thread(target=self._timed_event, args=(ev, self.loaded_at), daemon=True).start()

    def _timed_event(self, ev, loaded_at):
        time.sleep(float(ev["at"]) * self.scale)
        with self.lock:
            if self.loaded_at != loaded_at:
                return  # scenario replaced meanwhile
        self.action(ev["action"], source="scenario")

    def sim(self, seconds):
        return seconds * self.scale

    def log_event(self, kind, **fields):
        entry = {"t": now(), "time": iso(now()), "kind": kind, **fields}
        self.events.append(entry)
        return entry

    # -- printer behaviour --------------------------------------------------

    def state_line(self):
        if self.state == "feeding":
            return "Feeding Paper"
        if self.state == "printing":
            return "Printing " + self.layer
        if self.state == "error":
            return self.error_state_text or "Idle"
        return "Idle"

    def accept(self, job):
        """The backend sent the spool data: the printer now holds the job."""
        with self.lock:
            spec_index = len(self.received)
            spec = self.job_specs[spec_index] if spec_index < len(self.job_specs) else {}
            job = dict(job, spec=spec or {}, received_index=spec_index + 1, received_at=iso(now()))
            self.received.append(dict(job))
            self.held = job
            self.log_event("received", cups_job=job["cups_job"], index=spec_index + 1)
            self._start_run(job, delay=0.0)
            return job

    def _start_run(self, job, delay):
        gen = self.generation
        t = threading.Thread(target=self._run, args=(job, delay, gen), daemon=True)
        self.run_thread = t
        t.start()

    def _wait(self, seconds, gen):
        """Sleep simulated seconds; False if the run was aborted."""
        deadline = now() + self.sim(seconds)
        with self.lock:
            while True:
                if self.generation != gen:
                    return False
                left = deadline - now()
                if left <= 0:
                    return True
                self.cond.wait(left)

    def _set(self, state, layer=None):
        self.state = state
        self.layer = layer
        if state == "printing":
            self.last_layer_change = now()
        self.cond.notify_all()

    def _fail(self, job, code, state_text):
        self.state = "error"
        self.error = code
        self.error_state_text = state_text
        self.layer = None
        self.log_event("error", cups_job=job["cups_job"], code=code, text=ERRORS[code])
        if code == "0a":
            # Incorrect media: the backend cancels the job; nothing printed,
            # the printer drops it (R9, inferred from the code).
            self.held = None
        self.cond.notify_all()

    def _run(self, job, delay, gen):
        t = self.timing
        spec = job.get("spec") or {}
        fault = spec.get("fault")  # {code, layer, after}
        if delay and not self._wait(delay, gen):
            return
        with self.lock:
            if self.generation != gen:
                return
            self._set("feeding")
            self.log_event("feeding", cups_job=job["cups_job"])
        start = now()

        def wait_until(sim_t):
            return self._wait(max(0.0, sim_t - (now() - start) / self.scale), gen)

        # Physical checks while feeding.
        with self.lock:
            code = None
            if self.wrong_media or spec.get("error") == "0a":
                code = "0a"
            elif self.tray == "missing" and self.ink in ("missing",):
                code = "09"
            elif self.tray == "missing":
                code = "03"
            elif self.ink == "missing":
                code = "07"
            elif self.ink == "empty" or self.ink_prints == 0:
                code = "06"
            elif self.ink == "wrong":
                code = "08"
            elif self.tray == "empty" or self.paper_sheets == 0:
                code = "02"
            elif spec.get("error"):
                code = str(spec["error"]).lower()
        if code:
            if not wait_until(t["error_after"].get(code, 3.0)):
                return
            with self.lock:
                if self.generation == gen:
                    self._fail(job, code, "Feeding Paper")
            return
        with self.lock:
            if self.paper_sheets:
                self.paper_sheets -= 1
        for i, layer in enumerate(LAYERS):
            if not wait_until(t["layers"][i]):
                return
            with self.lock:
                if self.generation != gen:
                    return
                self._set("printing", layer)
                self.log_event("layer", cups_job=job["cups_job"], layer=layer)
            if fault and str(fault.get("layer", "")).upper() == layer:
                if not wait_until(t["layers"][i] + float(fault.get("after", 2.0))):
                    return
                with self.lock:
                    if self.generation == gen:
                        self._fail(job, str(fault["code"]).lower(), "Printing " + layer)
                return
        if not wait_until(t["eject"]):
            return
        with self.lock:
            if self.generation != gen:
                return
            if self.ink_prints:
                self.ink_prints -= 1
            out = {"t": now(), "time": iso(now()), "cups_job": job["cups_job"], "run": job.get("run")}
            self.outputs.append(out)
            self.log_event("photo_out", cups_job=job["cups_job"])
        if not wait_until(t["complete"]):
            return
        with self.lock:
            if self.generation != gen:
                return
            self.held = None
            self._set("idle")
            job["completed"] = True
            self.log_event("idle", cups_job=job["cups_job"])

    def action(self, name, source="test", **args):
        with self.lock:
            self.actions.append({"t": now(), "time": iso(now()), "action": name, "source": source, "args": args})
            self.log_event("action", action=name, source=source)
            if name in ("fix", "refill_paper", "insert_tray", "insert_ink", "replace_ink"):
                if name in ("fix", "refill_paper", "insert_tray"):
                    self.tray = "ok"
                    if "sheets" in args:
                        self.paper_sheets = int(args["sheets"])
                    elif self.paper_sheets == 0:
                        self.paper_sheets = None
                if name in ("fix", "insert_ink", "replace_ink"):
                    self.ink = "ok"
                    if "prints" in args:
                        self.ink_prints = int(args["prints"])
                    elif self.ink_prints == 0:
                        self.ink_prints = None
                if name == "fix":
                    self.wrong_media = False
                self._maybe_self_resume()
            elif name == "remove_tray":
                self.tray = "missing"
            elif name == "insert_empty_tray":
                self.tray = "empty"
            elif name == "remove_ink":
                self.ink = "missing"
            elif name == "print_button":
                if self.state == "error" and self.error in SELF_RESUMING and self.held:
                    self._resume_held(0.0)
            elif name == "power_cycle":
                # Clears the error and loses the held job (A4, P-1 job 123).
                self.generation += 1
                if self.held:
                    self.log_event("held_lost", cups_job=self.held["cups_job"])
                self.held = None
                self.error = None
                self.error_state_text = None
                self._set("idle")
            elif name == "unplug":
                self.connected = False
                self.generation += 1
                self.cond.notify_all()
            elif name == "plug":
                self.connected = True
                self.cond.notify_all()
            else:
                raise ValueError("unknown action: " + name)
            return self.snapshot()

    def _maybe_self_resume(self):
        if self.state != "error" or not self.held:
            if self.state == "error" and not self.held:
                self.error = None
                self._set("idle")
            return
        if self.error not in SELF_RESUMING:
            return  # needs a power cycle
        if not self.self_print:
            return  # this scenario's printer waits for its print button
        delay = self.timing["resume_after"].get(self.error, self.timing["resume_after"]["default"])
        self._resume_held(delay)

    def _resume_held(self, delay):
        job = self.held
        self.error = None
        self.error_state_text = None
        self._set("idle")
        self.log_event("self_resume", cups_job=job["cups_job"])
        self._start_run(job, delay)

    # -- query (-s) ------------------------------------------------------------

    def query(self, caller):
        """One standalone status query. Returns (lines, exit_code, sleep_s)."""
        with self.lock:
            entry = {"t": now(), "time": iso(now()), "caller": caller}
            queue_enabled = cups_queue_enabled()
            attached = [r for r in self.backend_runs if r.get("end") is None]
            if queue_enabled:
                self.violations.append({"time": iso(now()), "what": "printer query while the queue is enabled (FR-9.11)"})
            if attached:
                self.violations.append({"time": iso(now()), "what": "printer query while a CUPS backend is attached (FR-9.11)"})
            forced = self.query_forced.pop(0) if self.query_forced else None
            hang = None
            if forced is not None:
                answer = forced
            elif not self.connected:
                answer = {"fail": "not connected"}
            elif (
                self.query_hang_on_layer_change
                and self.state == "printing"
                and now() - self.last_layer_change < self.sim(1.5)
            ):
                answer = {"hang": self.query_hang_s}
            else:
                answer = None
            if isinstance(answer, dict) and "hang" in answer:
                hang = float(answer["hang"])
                lines = QUERY_PREAMBLE + QUERY_HANG_LINES
                code = self.query_exit_on_hang
                self.hang_pending_wrong_layer = self.query_wrong_layer_after_hang
                entry.update(result="hang", hang_s=hang)
            elif isinstance(answer, dict) and "fail" in answer:
                lines = [
                    "STATE: +connecting-to-device",
                    "STATE: +org.gutenprint.searching-for-device",
                    "ERROR: Printer open failure (No matching printers found!)",
                ]
                code = CUPS_BACKEND_FAILED
                entry.update(result="fail")
            else:
                if isinstance(answer, str):
                    state_text, error_text, media = self._forced_answer(answer)
                else:
                    state_text = self.state_line()
                    if self.hang_pending_wrong_layer and self.state == "printing":
                        nxt = LAYERS[min(LAYERS.index(self.layer) + 1, len(LAYERS) - 1)]
                        state_text = "Printing " + nxt
                        self.hang_pending_wrong_layer = False
                    error_text = ERRORS[self.error] if self.state == "error" and self.error else None
                    media = "None" if self.ink in ("missing",) else "P"
                lines = list(QUERY_PREAMBLE) + list(QUERY_PPD) + ["INFO: Printer state: " + state_text, "INFO: Media type: " + media]
                if error_text:
                    lines.append("INFO: Printer error: " + error_text)
                lines.append("STATE: -connecting-to-device")
                code = CUPS_BACKEND_OK
                entry.update(result="answer", state=state_text, error=error_text)
            entry["queue_enabled"] = queue_enabled
            self.queries.append(entry)
            return lines, code, (self.sim(hang) if hang else 0.0)

    @staticmethod
    def _forced_answer(answer):
        kind, _, arg = answer.partition(":")
        if kind == "idle":
            return "Idle", None, "P"
        if kind == "feeding":
            return "Feeding Paper", None, "P"
        if kind == "printing":
            return "Printing " + (arg or "YELLOW").upper(), None, "P"
        if kind == "error":
            return "Feeding Paper", arg or "Paper Feed", "P"
        raise ValueError("unknown forced query answer: " + answer)

    def snapshot(self):
        with self.lock:
            counts = {}
            for o in self.outputs:
                counts[o["cups_job"]] = counts.get(o["cups_job"], 0) + 1
            return {
                "state": self.state,
                "state_line": self.state_line(),
                "error": self.error and {"code": self.error, "text": ERRORS[self.error]},
                "held": self.held and {"cups_job": self.held["cups_job"]},
                "physical": {
                    "tray": self.tray,
                    "ink": self.ink,
                    "paper_sheets": self.paper_sheets,
                    "ink_prints": self.ink_prints,
                    "connected": self.connected,
                },
                "scale": self.scale,
                "received": self.received,
                "outputs": self.outputs,
                "duplicates": sorted(j for j, n in counts.items() if n > 1),
                "backend_runs": self.backend_runs,
                "queries": self.queries,
                "violations": self.violations,
                "actions": self.actions,
                "events": self.events,
                "scenario": self.scenario.get("id"),
            }


PRINTER = Printer()


def cups_queue_enabled():
    try:
        out = subprocess.run(["lpstat", "-p", QUEUE], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    first = out.splitlines()[0] if out else ""
    return "disabled" not in first


# ---------------------------------------------------------------------------
# Backend session (daemon side)
# ---------------------------------------------------------------------------


def backend_session(conn):
    """Drive one CUPS backend process: stream stderr lines, then the exit code."""
    f = conn.makefile("rw", buffering=1, encoding="utf-8", newline="\n")
    hello = json.loads(f.readline())
    p = PRINTER
    run = {
        "cups_job": hello["cups_job"],
        "user": hello.get("user"),
        "title": hello.get("title"),
        "copies": hello.get("copies"),
        "options": hello.get("options"),
        "fast_return": hello["fast_return"],
        "fast_return_env": hello.get("fast_return_env"),
        "data_sha256": hello.get("data_sha256"),
        "data_bytes": hello.get("data_bytes"),
        "start": iso(now()),
        "end": None,
        "exit": None,
        "messages": [],
    }
    with p.lock:
        p.backend_runs.append(run)
        run["run"] = len(p.backend_runs)

    def emit(line):
        run["messages"].append({"time": iso(now()), "line": line})
        f.write(json.dumps({"line": line}) + "\n")
        f.flush()

    def finish(code):
        run["exit"] = code
        run["end"] = iso(now())
        f.write(json.dumps({"exit": code}) + "\n")
        f.flush()

    try:
        for line in [
            "STATE: +connecting-to-device",
            "STATE: +org.gutenprint.searching-for-device",
        ]:
            emit(line)
        # Printer not connected: the backend keeps searching (assumed: the
        # real behaviour without the printer was not recorded).
        while True:
            with p.lock:
                if p.connected:
                    break
            emit("STATE: +org.gutenprint.searching-for-device")
            time.sleep(p.sim(p.timing["report_interval"]))
        for line in [
            "STATE: -org.gutenprint.searching-for-device",
            "STATE: -offline-report",
            "STATE: -connecting-to-device",
            "INFO: Printing started (1 copies)",
            "STATE: -media-empty",
            "INFO: Waiting for printer idle",
        ]:
            emit(line)
        # Wait for the printer to be idle (a print it is still doing by itself).
        while True:
            with p.lock:
                if not p.connected:
                    emit("ERROR: Failure to receive data from printer (libusb error -4: (0/12 from 0x82))")
                    finish(CUPS_BACKEND_FAILED)
                    return
                if p.state == "error":
                    code = p.error
                    emit("ERROR: Printer error: %s (%s)" % (ERRORS[code], code))
                    if code in MEDIA_EMPTY:
                        emit("STATE: +media-empty")
                    emit("STATE: -connecting-to-device")
                    finish(CUPS_BACKEND_CANCEL if code == "0a" else CUPS_BACKEND_STOP)
                    return
                if p.state == "idle":
                    break
            time.sleep(p.sim(p.timing["report_interval"]))
        time.sleep(p.sim(p.timing["spool"]))
        emit("INFO: Sending spool data")
        job = p.accept({"cups_job": run["cups_job"], "run": run["run"]})
        emit("INFO: Waiting for printer acknowledgement")
        while True:
            with p.lock:
                p.cond.wait(p.sim(p.timing["report_interval"]))
                connected = p.connected
                mine = p.held is job
                state, layer, err = p.state, p.layer, p.error
                done = job.get("completed")
            if not connected:
                emit("ERROR: Failure to receive data from printer (libusb error -4: (0/12 from 0x82))")
                finish(CUPS_BACKEND_FAILED)
                return
            if done:
                emit("INFO: Print complete (0 copies remaining)")
                emit("STATE: -media-empty")
                emit("STATE: -connecting-to-device")
                finish(CUPS_BACKEND_OK)
                return
            if state == "error" and (mine or err == "0a"):
                emit("ERROR: Printer error: %s (%s)" % (ERRORS[err], err))
                if err in MEDIA_EMPTY:
                    emit("STATE: +media-empty")
                emit("STATE: -connecting-to-device")
                finish(CUPS_BACKEND_CANCEL if err == "0a" else CUPS_BACKEND_STOP)
                return
            if not mine and state != "error":
                # Power cycle dropped the job while the backend waited (assumed).
                emit("ERROR: Printer error: Unknown Error (00)")
                finish(CUPS_BACKEND_STOP)
                return
            line = "Printer state: " + ("Feeding Paper" if state == "feeding" else "Printing " + layer if state == "printing" else "Idle")
            emit("INFO: " + line)
            if state == "printing" and run["fast_return"]:
                # Fast return: the backend leaves as soon as printing starts
                # (P-1 job 118), the printer goes on alone.
                emit("INFO: Fast return mode enabled.")
                emit("INFO: Print complete (0 copies remaining)")
                emit("STATE: -media-empty")
                emit("STATE: -connecting-to-device")
                finish(CUPS_BACKEND_OK)
                return
    except (BrokenPipeError, ConnectionResetError, OSError):
        # CUPS killed the backend (job cancelled): the printer keeps the job.
        run["end"] = iso(now())
        run["exit"] = "killed"
    except Exception:
        traceback.print_exc()
        run["end"] = iso(now())
        run["exit"] = "sim-error"
    finally:
        try:
            conn.close()
        except OSError:
            pass


def backend_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", BACKEND_PORT))
    srv.listen(16)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=backend_session, args=(conn,), daemon=True).start()


# ---------------------------------------------------------------------------
# Control API (tests) and query relay (booth container)
# ---------------------------------------------------------------------------


def run_cmd(args, timeout=30, input_bytes=None):
    r = subprocess.run(args, capture_output=True, timeout=timeout, input=input_bytes)
    return {
        "cmd": args,
        "exit": r.returncode,
        "stdout": r.stdout.decode("utf-8", "replace"),
        "stderr": r.stderr.decode("utf-8", "replace"),
    }


FILES_CONF = "/etc/cups/cups-files.conf"
DEPLOYED_FILES_CONF = None  # read at daemon start


def reload_cupsd():
    """SIGHUP: cupsd rereads cupsd.conf and cups-files.conf."""
    hup = run_cmd(["pkill", "-HUP", "-x", "cupsd"])
    time.sleep(0.5)
    deadline = now() + 20
    while now() < deadline and run_cmd(["lpstat", "-r"])["exit"] != 0:
        time.sleep(0.2)
    return hup


def cups_status():
    return {
        "printer": run_cmd(["lpstat", "-l", "-p", QUEUE])["stdout"],
        "accepting": run_cmd(["lpstat", "-a", QUEUE])["stdout"],
        "device": run_cmd(["lpstat", "-v", QUEUE])["stdout"],
        "jobs_not_completed": run_cmd(["lpstat", "-W", "not-completed", "-o", QUEUE])["stdout"],
        "jobs_all": run_cmd(["lpstat", "-W", "all", "-l", "-o", QUEUE])["stdout"],
        "enabled": cups_queue_enabled(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "selphy-sim/1"

    def log_message(self, fmt, *args):
        sys.stderr.write("control: " + (fmt % args) + "\n")

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _json(self, code, obj):
        data = json.dumps(obj, indent=1, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _text(self, code, text):
        data = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            if url.path == "/health":
                return self._json(200, {"ok": True, "queue": cups_status()["device"]})
            if url.path == "/state":
                return self._json(200, PRINTER.snapshot())
            if url.path == "/query":
                # Relay for the booth container's `gutenprint53+usb -s` shim.
                lines, code, sleep_s = PRINTER.query(qs.get("caller", ["?"])[0])
                if sleep_s:
                    time.sleep(sleep_s)
                return self._json(200, {"lines": lines, "exit": code})
            if url.path == "/cups/status":
                return self._json(200, cups_status())
            if url.path == "/cups/error_log":
                with open("/var/log/cups/error_log", encoding="utf-8", errors="replace") as fh:
                    return self._text(200, fh.read())
            if url.path == "/cups/files-conf":
                with open("/etc/cups/cups-files.conf", encoding="utf-8") as fh:
                    return self._text(200, fh.read())
            return self._json(404, {"error": "not found"})
        except Exception as exc:
            traceback.print_exc()
            return self._json(500, {"error": str(exc)})

    def do_POST(self):
        try:
            url = urlparse(self.path)
            body = self._body()
            if url.path == "/scenario":
                scenario = yaml.safe_load(body) if body else {}
                PRINTER.reset(scenario or {})
                return self._json(200, {"loaded": (scenario or {}).get("id"), "scale": PRINTER.scale})
            if url.path == "/action":
                req = json.loads(body or b"{}")
                name = req.pop("action")
                return self._json(200, PRINTER.action(name, **req))
            if url.path == "/reset":
                # Clean rig: deployed cups-files.conf, no jobs, queue enabled
                # and accepting, default printer.
                out = []
                with open(FILES_CONF, encoding="utf-8") as fh:
                    if fh.read() != DEPLOYED_FILES_CONF:
                        with open(FILES_CONF, "w", encoding="utf-8") as w:
                            w.write(DEPLOYED_FILES_CONF)
                        out.append(reload_cupsd())
                out += [run_cmd(["cancel", "-a", "-x", QUEUE]), run_cmd(["cupsenable", QUEUE]), run_cmd(["cupsaccept", QUEUE])]
                PRINTER.reset({})
                return self._json(200, {"ok": all(r["exit"] == 0 for r in out), "steps": out})
            if url.path == "/cups/lp":
                # Test helper: submit a job the way Photobooth does (lp -o landscape -o fit-to-page).
                req = parse_qs(urlparse(self.path).query)
                path = "/opt/selphy-sim/test-photo.jpg"  # a JPEG, like the booth's photos
                if body and self.headers.get("Content-Type", "").startswith("image/"):
                    path = "/tmp/sim-upload-%d.jpg" % int(now() * 1000)
                    with open(path, "wb") as fh:
                        fh.write(body)
                opts = req.get("o", ["landscape", "fit-to-page"])
                args = ["lp", "-d", QUEUE]
                for o in opts:
                    args += ["-o", o]
                return self._json(200, run_cmd(args + [path]))
            if url.path == "/cups/fast-return":
                # Test hook for NFR-13.2/13.4 and the simulator's own checks:
                # set or remove `SetEnv FAST_RETURN <value>` in cups-files.conf
                # and make cupsd reread its configuration (SIGHUP).
                req = json.loads(body or b"{}")
                conf = FILES_CONF
                with open(conf, encoding="utf-8") as fh:
                    lines = [l for l in fh.read().splitlines() if not l.startswith("SetEnv FAST_RETURN ")]
                if req.get("value") is not None:
                    lines.append("SetEnv FAST_RETURN %d" % int(req["value"]))
                with open(conf, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
                hup = reload_cupsd()
                return self._json(200, {"ok": hup["exit"] == 0, "value": req.get("value")})
            if url.path in ("/cups/enable", "/cups/disable", "/cups/cancel", "/cups/reject", "/cups/accept"):
                req = json.loads(body or b"{}")
                if url.path == "/cups/enable":
                    return self._json(200, run_cmd(["cupsenable", QUEUE]))
                if url.path == "/cups/disable":
                    return self._json(200, run_cmd(["cupsdisable", QUEUE]))
                if url.path == "/cups/reject":
                    return self._json(200, run_cmd(["cupsreject", QUEUE]))
                if url.path == "/cups/accept":
                    return self._json(200, run_cmd(["cupsaccept", QUEUE]))
                job = req.get("job")
                return self._json(200, run_cmd(["cancel", "%s-%s" % (QUEUE, job)] if job else ["cancel", "-a", QUEUE]))
            return self._json(404, {"error": "not found"})
        except Exception as exc:
            traceback.print_exc()
            return self._json(500, {"error": str(exc)})


def daemon():
    global DEPLOYED_FILES_CONF
    if yaml is None:
        sys.exit("selphy_sim daemon needs PyYAML (python3-yaml)")
    with open(FILES_CONF, encoding="utf-8") as fh:
        DEPLOYED_FILES_CONF = fh.read()
    threading.Thread(target=backend_server, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", CONTROL_PORT), Handler)
    sys.stderr.write("selphy-sim: control API on :%d, backend port %d\n" % (CONTROL_PORT, BACKEND_PORT))
    srv.serve_forever()


# ---------------------------------------------------------------------------
# Client sides: CUPS backend and status query
# ---------------------------------------------------------------------------


def backend_main(argv):
    out = sys.stderr
    if len(argv) == 0:
        # Device discovery (lpinfo -v / the setup's find_printer).
        print('direct %s "Canon CP1300" "Canon SELPHY CP1300 (simulated)" "MFG:Canon;MDL:CP1300;" ""' % DEVICE_URI)
        return 0
    if len(argv) not in (5, 6):
        out.write("ERROR: Usage: sim-selphy job user title copies options [file]\n")
        return CUPS_BACKEND_FAILED
    job, user, title, copies, options = argv[:5]
    data = open(argv[5], "rb").read() if len(argv) == 6 else sys.stdin.buffer.read()
    env = os.environ.get("FAST_RETURN")
    # Gutenprint under CUPS: fast return is on unless FAST_RETURN says 0 (§2.2).
    try:
        fast_return = True if env is None else int(env) != 0
    except ValueError:
        fast_return = True
    hello = {
        "cups_job": int(job),
        "user": user,
        "title": title,
        "copies": copies,
        "options": options,
        "fast_return": fast_return,
        "fast_return_env": env,
        "data_sha256": hashlib.sha256(data).hexdigest(),
        "data_bytes": len(data),
    }
    out.write("DEBUG: Multi-Call Dye-sublimation CUPS Backend version 0.123G (selphy-sim)\n")
    out.flush()
    try:
        sock = socket.create_connection((DAEMON_HOST, BACKEND_PORT), timeout=10)
    except OSError as exc:
        out.write("ERROR: selphy-sim daemon not reachable: %s\n" % exc)
        return CUPS_BACKEND_FAILED
    sock.settimeout(None)
    f = sock.makefile("rw", buffering=1, encoding="utf-8", newline="\n")
    f.write(json.dumps(hello) + "\n")
    f.flush()
    for raw in f:
        msg = json.loads(raw)
        if "line" in msg:
            out.write(msg["line"] + "\n")
            out.flush()
        elif "exit" in msg:
            return int(msg["exit"])
    out.write("ERROR: selphy-sim daemon closed the connection\n")
    return CUPS_BACKEND_FAILED


def query_main(argv):
    """`gutenprint53+usb -s` stand-in: ask the daemon (locally or over the network)."""
    import urllib.request

    host = os.environ.get("SIM_QUERY_URL", "http://%s:%d" % (DAEMON_HOST, CONTROL_PORT))
    caller = socket.gethostname()
    try:
        with urllib.request.urlopen(host + "/query?caller=" + caller, timeout=600) as r:
            res = json.loads(r.read())
    except OSError as exc:
        sys.stderr.write("ERROR: Printer open failure (selphy-sim unreachable: %s)\n" % exc)
        return CUPS_BACKEND_FAILED
    for line in res["lines"]:
        sys.stderr.write(line + "\n")
    return int(res["exit"])


def main():
    prog = os.path.basename(sys.argv[0])
    args = sys.argv[1:]
    if prog == "sim-selphy":
        return backend_main(args)
    if prog.startswith("gutenprint53+usb"):
        # Installed in place of the real Gutenprint USB backend: discovery for
        # the setup's entrypoint, and the standalone status query.
        if not args:
            print('direct gutenprint53+usb://canon-cp1300/SIM0001 "Canon CP1300" "Canon SELPHY CP1300 (simulated)" "MFG:Canon;MDL:CP1300;" ""')
            return 0
        if args == ["-s"]:
            return query_main(args)
        sys.stderr.write("ERROR: the simulated stack prints through sim-selphy://, not gutenprint53+usb\n")
        return CUPS_BACKEND_FAILED
    if not args:
        sys.stderr.write(__doc__)
        return 2
    cmd, rest = args[0], args[1:]
    if cmd == "daemon":
        return daemon()
    if cmd == "backend":
        return backend_main(rest)
    if cmd == "query":
        return query_main(rest)
    sys.stderr.write(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
