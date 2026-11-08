#!/usr/bin/env python3
"""Server side of the car link.

Waits for the car (the Pi) to connect. For every frame it receives, it calls
hookup.decide() (see hookup.py), sends the motor command back, and records the
frame, label, score and command in a folder for that connection.

Only one car can be connected at a time; extra connections are refused.

Usage
    python3 server.py                  # uses server_config.json next to this file
    python3 server.py --config other.json --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# protocol.py is shared with the Pi and lives in ../shared
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
import hookup  # noqa: E402
import protocol as P  # noqa: E402

log = logging.getLogger("server")

DEFAULTS = {
    "listen": {"host": "0.0.0.0", "port": P.DEFAULT_PORT},
    "password": P.PLACEHOLDER_PASSWORD,
    "idle_timeout_s": 10.0,           # no frame this long -> drop the connection
    "max_frame_bytes": P.DEFAULT_MAX_PAYLOAD_BYTES,
    "recording": {"enabled": True, "directory": "recordings"},
    "decider": {
        "backend": "dummy",           # "dummy", "python" or "http" (see hookup.py)
        "target": "",                 # python backend: "module_name:function_name"
        "url": "",                    # http backend: the service's URL
        "timeout_s": 2.0,             # http backend: how long to wait for an answer
        "dummy_throttle": 0.0,        # dummy backend: value sent to all four motors
        "options": {},                # python backend: passed to the module's configure(options)
    },
}


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


class SessionRecorder:
    """Saves every frame of one connection, plus a log line per frame, in one folder:

        recordings/2026-10-26_19-30-05_192-168-1-60/
            session.json                          connection details
            log.jsonl                             one JSON line per frame
            frame_000001_19-30-05.123.jpg
            frame_000002_19-30-05.201.jpg
            ...
    """

    def __init__(self, root: Path, addr: tuple, info: dict):
        started = time.time()
        name = (datetime.fromtimestamp(started).strftime("%Y-%m-%d_%H-%M-%S")
                + "_" + str(addr[0]).replace(".", "-").replace(":", "-"))
        self.dir = root / name
        suffix = 1
        while self.dir.exists():
            suffix += 1
            self.dir = root / f"{name}_{suffix}"
        self.dir.mkdir(parents=True)
        self.name = self.dir.name
        self.session = {"client": f"{addr[0]}:{addr[1]}", "started": _iso(started),
                        "ended": None, "frames": 0, **info}
        self._write_session()
        self.log = open(self.dir / "log.jsonl", "a", encoding="utf-8", buffering=1)
        self.enabled = True

    def _write_session(self) -> None:
        (self.dir / "session.json").write_text(json.dumps(self.session, indent=2) + "\n",
                                               encoding="utf-8")

    def record(self, image: bytes, meta: dict, decision: hookup.Decision,
               decide_ms: float, error: str | None, last_rtt_ms) -> None:
        if not self.enabled:
            return
        try:
            stamp = datetime.fromtimestamp(meta["received_time"]).strftime("%H-%M-%S.%f")[:-3]
            filename = (f"frame_{meta['seq']:06d}_{stamp}"
                        + P.FILE_EXTENSIONS.get(meta["format"], ".bin"))
            (self.dir / filename).write_bytes(image)
            entry = {
                "seq": meta["seq"],
                "file": filename,
                "capture_time": _iso(meta["capture_time"]) if meta.get("capture_time") else None,
                "received_time": _iso(meta["received_time"]),
                "label": decision.label,
                "score": decision.score,
                "motors": decision.motors,
                "details": decision.details,
                "decide_ms": round(decide_ms, 1),
                "previous_round_trip_ms": last_rtt_ms,
                "format": meta["format"],
                "width": meta["width"],
                "height": meta["height"],
                "error": error,
            }
            self.log.write(json.dumps(entry) + "\n")
            self.session["frames"] += 1
        except OSError as exc:
            log.error("recording failed (%s); recording turned off for this connection", exc)
            self.enabled = False

    def close(self) -> None:
        try:
            self.log.close()
            self.session["ended"] = _iso(time.time())
            self._write_session()
        except OSError as exc:
            log.error("could not finish the recording: %s", exc)


def handle_car(conn: socket.socket, addr: tuple, cfg: dict, password: str) -> None:
    conn.settimeout(float(cfg["idle_timeout_s"]))
    P.server_handshake(conn, password)
    log.info("car %s:%s authenticated", *addr[:2])

    recorder = None
    if cfg["recording"]["enabled"]:
        recorder = SessionRecorder(Path(cfg["recording"]["directory"]), addr,
                                   {"decider": cfg["decider"]["backend"],
                                    "decider_target": cfg["decider"]["target"]
                                    if cfg["decider"]["backend"] == "python" else None,
                                    "protocol_version": P.PROTOCOL_VERSION})
        log.info("recording to %s", recorder.dir)

    frames = errors = 0
    try:
        while True:
            header, image = P.recv_message(conn, max_payload=int(cfg["max_frame_bytes"]))
            received = time.time()
            if header.get("type") != "frame":
                raise P.ProtocolError(f"expected a 'frame', got {header.get('type')!r}")
            fmt = header.get("format")
            if fmt not in P.IMAGE_FORMATS:
                raise P.ProtocolError(f"unknown image format {fmt!r}")
            meta = {
                "seq": int(header.get("seq", 0)),
                "format": fmt,
                "width": int(header.get("width", 0)),
                "height": int(header.get("height", 0)),
                "channels": int(header.get("channels", 3)),
                "capture_time": header.get("capture_time"),
                "received_time": received,
                "session": recorder.name if recorder else None,
                "session_dir": str(recorder.dir) if recorder else None,
            }

            started = time.perf_counter()
            error = None
            try:
                decision = hookup.normalize(hookup.decide(image, dict(meta)))
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                decision = hookup.Decision.stop(label="error")
                errors += 1
                if errors <= 5 or errors % 100 == 0:
                    log.error("decide() failed on frame %d (%s); sent a stop command",
                              meta["seq"], error)
            decide_ms = (time.perf_counter() - started) * 1000

            # Reply first, record afterwards, so disk writes never delay the car.
            P.send_message(conn, {"type": "command", "seq": meta["seq"],
                                  "label": decision.label, "score": decision.score,
                                  "motors": decision.motors})
            frames += 1
            log.debug("frame %d: %s (%.2f) %s, decide %.0f ms", meta["seq"],
                      decision.label, decision.score, decision.motors, decide_ms)
            if recorder:
                recorder.record(image, meta, decision, decide_ms, error,
                                header.get("last_rtt_ms"))
    finally:
        if recorder:
            recorder.close()
        log.info("car %s:%s session ended after %d frames (%d errors)",
                 addr[0], addr[1], frames, errors)


def serve(cfg: dict, password: str) -> None:
    busy = threading.Lock()

    def run(conn: socket.socket, addr: tuple) -> None:
        try:
            handle_car(conn, addr, cfg, password)
        except P.AuthError:
            log.warning("connection from %s failed the password check", addr[0])
        except socket.timeout:
            log.warning("car %s sent nothing for %.0f s; dropping it",
                        addr[0], float(cfg["idle_timeout_s"]))
        except (ConnectionError, P.ProtocolError, OSError) as exc:
            log.warning("car %s disconnected: %s", addr[0], exc)
        finally:
            conn.close()
            busy.release()

    host, port = cfg["listen"]["host"], int(cfg["listen"]["port"])
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(2)
        log.info("listening on %s:%d", host, port)
        while True:
            conn, addr = srv.accept()
            P.configure_socket(conn)
            if not busy.acquire(blocking=False):
                log.warning("refused %s:%s: another car is already connected", *addr[:2])
                P.send_error(conn, "busy", "another car is already connected")
                conn.close()
                continue
            log.info("connection from %s:%s", *addr[:2])
            threading.Thread(target=run, args=(conn, addr), name="car", daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Server side of the car link")
    parser.add_argument("--config", default=str(Path(__file__).with_name("server_config.json")))
    parser.add_argument("--verbose", action="store_true", help="log every frame")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    cfg = P.load_config(args.config if Path(args.config).exists() else None, DEFAULTS)
    password = P.resolve_password(cfg["password"])
    log.info("decider: %s", hookup.configure(cfg["decider"]))
    try:
        serve(cfg, password)
    except KeyboardInterrupt:
        log.info("stopping")


if __name__ == "__main__":
    main()
