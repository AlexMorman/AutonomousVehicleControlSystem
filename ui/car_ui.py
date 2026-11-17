#!/usr/bin/env python3
"""Car UI: a web page to watch the camera, take pictures, record and drive.

This program sits between the Pi and server.py. Neither of them is changed:

    Pi (pi_client.py) ──▶ car_ui.py (port 5006) ──▶ server.py (port 5005)
                               │
                               └─▶ web page  http://<laptop-ip>:8080

For every frame the Pi sends, car_ui.py
  1. keeps a copy for the web page (and for pictures / recordings),
  2. if AI is on, forwards the frame to server.py and reads the model's answer,
  3. sends the Pi a motor command chosen by the mode set on the page:
        auto    the model's motor values (stop if AI is off)
        hold    stop (the model still runs if AI is on, so you see its answer)
        manual  the buttons / keys pressed on the page; stop if the page goes
                quiet for manual_timeout_s (tab closed, Wi-Fi dropped)

To use it, point the Pi's server.port at 5006 (server.host stays the laptop's
IP). To stop using it, point the Pi back at 5005: nothing else changes.

Usage
    python3 car_ui.py                    # uses car_ui_config.json next to this file
    python3 car_ui.py --config other.json --verbose

Uses only the standard library plus ../shared/protocol.py.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import socket
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

UI_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(UI_DIR.parent / "shared"))
import protocol as P  # noqa: E402

log = logging.getLogger("car_ui")

DEFAULTS = {
    "car_listen": {"host": "0.0.0.0", "port": 5006},     # the Pi connects here
    "server": {"host": "127.0.0.1", "port": P.DEFAULT_PORT,  # where server.py runs
               "connect_timeout_s": 1.0, "reply_timeout_s": 2.0, "retry_s": 2.0},
    "web": {"host": "0.0.0.0", "port": 8080},
    "password": "",                       # car link only; "" = take it from server_config
    "server_config": "../server/server_config.json",  # where that password is read from
    "idle_timeout_s": 10.0,               # no frame from the Pi this long -> drop it
    "start_mode": "hold",                 # auto, hold or manual
    "start_ai": True,                     # forward frames to server.py at startup
    "manual_timeout_s": 0.5,              # manual mode: page quiet this long -> stop
    "snapshots_dir": "snapshots",         # relative paths are inside the ui/ folder
    "clips_dir": "clips",
}

MODES = ("auto", "hold", "manual")
STOP = {name: 0.0 for name in P.MOTOR_NAMES}


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


def _clean_name(name: str) -> str:
    """User-typed name -> safe file name part ('' if nothing usable)."""
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name or "").strip())
    return name.strip("_-")[:60]


def _show(path: Path) -> str:
    """Path for messages: relative to ui/ when inside it, else absolute."""
    try:
        return str(path.relative_to(UI_DIR))
    except ValueError:
        return str(path)


def _next_number(folder: Path) -> int:
    """1 + the highest NNN_ prefix in the folder, so numbering survives restarts."""
    highest = 0
    if folder.exists():
        for entry in folder.iterdir():
            m = re.match(r"(\d+)_", entry.name)
            if m:
                highest = max(highest, int(m.group(1)))
    return highest + 1


def _numbered_path(root: Path, name: str, suffix: str = "") -> Path:
    """root/YYYY-MM-DD/NNN_HH-MM-SS[_name]suffix: sorting by name = sorting by time."""
    now = datetime.now()
    day = root / now.strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    stem = f"{_next_number(day):03d}_{now.strftime('%H-%M-%S')}"
    if name:
        stem += f"_{name}"
    return day / (stem + suffix)


def _motors_from(value) -> dict:
    """Validate four motor values from the page; clamp each to -1..1."""
    if not isinstance(value, dict):
        raise ValueError("motors must be an object")
    out = {}
    for name in P.MOTOR_NAMES:
        x = float(value.get(name, 0.0))
        if x != x:  # NaN
            raise ValueError(f"{name} is not a number")
        out[name] = max(-1.0, min(1.0, x))
    return out


class Recorder:
    """One clip: every frame received while recording, plus a log line per frame.

        clips/2026-11-17/001_19-30-05_name/
            clip.json        start/end, frame count
            log.jsonl        one line per frame: mode, AI, label, motors sent
            frame_000001.jpg ...
    """

    def __init__(self, root: Path, name: str):
        self.dir = _numbered_path(root, name)
        self.dir.mkdir()
        self.started = time.time()
        self.frames = 0
        self.info = {"name": name, "started": _iso(self.started), "ended": None, "frames": 0}
        self._write_info()
        self.log = open(self.dir / "log.jsonl", "a", encoding="utf-8", buffering=1)

    def _write_info(self) -> None:
        (self.dir / "clip.json").write_text(json.dumps(self.info, indent=2) + "\n",
                                            encoding="utf-8")

    def add(self, image: bytes, entry: dict) -> None:
        self.frames += 1
        filename = f"frame_{self.frames:06d}" + P.FILE_EXTENSIONS.get(entry["format"], ".bin")
        (self.dir / filename).write_bytes(image)
        self.log.write(json.dumps({"file": filename, **entry}) + "\n")

    def close(self) -> None:
        self.log.close()
        self.info.update(ended=_iso(time.time()), frames=self.frames)
        self._write_info()


class State:
    """Everything the car thread and the web page share. Guarded by one lock."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.new_frame = threading.Condition(self.lock)
        self.mode = cfg["start_mode"] if cfg["start_mode"] in MODES else "hold"
        self.ai = bool(cfg["start_ai"])
        self.manual = dict(STOP)
        self.manual_at = 0.0          # monotonic time of the page's last manual message
        self.stop_size_px = None      # page's override of the stop size (px); None = server's config value
        self.server_info = {}         # stop_size_px / px_to_inches as last reported by server.py
        self.car = None               # "ip:port" while the Pi is connected
        self.server_ok = False
        self.server_error = None
        self.frame = None             # newest image bytes
        self.frame_meta = {}
        self.frame_id = 0
        self.last = {}                # what happened on the newest frame (for the page)
        self.fps = 0.0
        self.recorder: Recorder | None = None
        self.snapshots_root = UI_DIR / cfg["snapshots_dir"]  # absolute paths stay absolute
        self.clips_root = UI_DIR / cfg["clips_dir"]

    # ---- called by the car thread --------------------------------------
    def choose_motors(self, model_motors: dict | None) -> tuple[dict, str]:
        """The command to send the Pi, and why. Caller holds no lock."""
        with self.lock:
            if self.mode == "auto":
                if model_motors is not None:
                    return model_motors, "auto: model"
                return dict(STOP), "auto: no model answer (AI off or server down) -> stop"
            if self.mode == "manual":
                quiet = time.monotonic() - self.manual_at
                if quiet <= float(self.cfg["manual_timeout_s"]):
                    return dict(self.manual), "manual"
                return dict(STOP), "manual: page quiet -> stop"
            return dict(STOP), "hold"

    def publish(self, image: bytes, meta: dict, last: dict) -> None:
        with self.lock:
            self.frame = image
            self.frame_meta = meta
            self.frame_id += 1
            self.last = last
            rec = self.recorder
            self.new_frame.notify_all()
        if rec:
            try:
                rec.add(image, {**last, "format": meta["format"]})
            except (OSError, ValueError) as exc:
                log.error("recording failed (%s); recording stopped", exc)
                self.stop_recording()

    # ---- called by the web page ----------------------------------------
    def snapshot(self, name: str) -> dict:
        with self.lock:
            image, meta, last = self.frame, dict(self.frame_meta), dict(self.last)
        if image is None:
            raise ValueError("no frame yet: is the car connected?")
        clean = _clean_name(name)
        path = _numbered_path(self.snapshots_root, clean,
                              P.FILE_EXTENSIONS.get(meta.get("format"), ".bin"))
        path.write_bytes(image)
        path.with_suffix(".json").write_text(
            json.dumps({"name": clean, "saved": _iso(time.time()), "frame": meta, **last},
                       indent=2) + "\n", encoding="utf-8")
        log.info("picture saved: %s", path)
        return {"file": _show(path)}

    def start_recording(self, name: str) -> dict:
        with self.lock:
            if self.recorder:
                raise ValueError("already recording")
            self.recorder = Recorder(self.clips_root, _clean_name(name))
            path = self.recorder.dir
        log.info("recording to %s", path)
        return {"dir": _show(path)}

    def stop_recording(self) -> dict:
        with self.lock:
            rec, self.recorder = self.recorder, None
        if not rec:
            return {}
        rec.close()
        log.info("recording stopped: %s (%d frames)", rec.dir, rec.frames)
        return {"dir": _show(rec.dir), "frames": rec.frames}

    def status(self) -> dict:
        with self.lock:
            rec = self.recorder
            return {
                "mode": self.mode,
                "ai": self.ai,
                "car": self.car,
                "server_ok": self.server_ok,
                "server_error": self.server_error,
                "fps": round(self.fps, 1),
                "frame_id": self.frame_id,
                "frame": self.frame_meta,
                "last": self.last,
                "manual_timeout_s": self.cfg["manual_timeout_s"],
                "stop_size_px": self.stop_size_px or self.server_info.get("stop_size_px"),
                "px_to_inches": self.server_info.get("px_to_inches"),
                "recording": None if not rec else {
                    "dir": _show(rec.dir), "frames": rec.frames,
                    "seconds": round(time.time() - rec.started)},
            }


# ==========================================================================
# Car side: the Pi connects here; frames are forwarded to server.py
# ==========================================================================

class Upstream:
    """The connection to server.py, opened when needed and dropped on any error."""

    def __init__(self, cfg: dict, password: str, state: State):
        self.cfg = cfg["server"]
        self.password = password
        self.state = state
        self.sock: socket.socket | None = None
        self.next_try = 0.0

    def _set(self, ok: bool, error: str | None) -> None:
        with self.state.lock:
            self.state.server_ok, self.state.server_error = ok, error

    def close(self) -> None:
        if self.sock:
            self.sock.close()
            self.sock = None
        self._set(False, None)

    def _connect(self) -> bool:
        if time.monotonic() < self.next_try:
            return False
        host, port = self.cfg["host"], int(self.cfg["port"])
        try:
            sock = socket.create_connection((host, port),
                                            timeout=float(self.cfg["connect_timeout_s"]))
            P.configure_socket(sock)
            sock.settimeout(float(self.cfg["reply_timeout_s"]))
            P.client_handshake(sock, self.password)
        except P.AuthError:
            self._fail("server.py rejected the password (check car_ui_config.json)")
            return False
        except (OSError, P.ProtocolError) as exc:
            self._fail(f"can't reach server.py at {host}:{port}: {exc}")
            return False
        self.sock = sock
        self._set(True, None)
        log.info("connected to server.py at %s:%d", host, port)
        return True

    def _fail(self, error: str) -> None:
        if self.sock:
            self.sock.close()
            self.sock = None
        self.next_try = time.monotonic() + float(self.cfg["retry_s"])
        with self.state.lock:
            changed = self.state.server_error != error
        if changed:
            log.warning("%s; frames get a stop command until it's back", error)
        self._set(False, error)

    def ask(self, header: dict, image: bytes) -> dict | None:
        """Forward one frame; return server.py's command, or None if unavailable."""
        if self.sock is None and not self._connect():
            return None
        try:
            P.send_message(self.sock, header, image)
            while True:
                reply, _ = P.recv_message(self.sock, max_payload=0)
                if reply.get("type") == "command" and reply.get("seq") == header.get("seq"):
                    return reply
        except socket.timeout:
            self._fail(f"server.py took longer than {self.cfg['reply_timeout_s']} s to answer")
        except (OSError, P.ProtocolError) as exc:
            self._fail(f"lost server.py: {exc}")
        return None


def handle_car(conn: socket.socket, addr: tuple, cfg: dict, password: str,
               state: State) -> None:
    conn.settimeout(float(cfg["idle_timeout_s"]))
    P.server_handshake(conn, password)
    log.info("car %s:%s connected", *addr[:2])
    with state.lock:
        state.car = f"{addr[0]}:{addr[1]}"
    upstream = Upstream(cfg, password, state)
    window_start, window_frames = time.monotonic(), 0
    try:
        while True:
            header, image = P.recv_message(conn, max_payload=P.DEFAULT_MAX_PAYLOAD_BYTES)
            received = time.time()
            if header.get("type") != "frame":
                raise P.ProtocolError(f"expected a 'frame', got {header.get('type')!r}")
            seq = header.get("seq")

            with state.lock:
                ai = state.ai
                if state.stop_size_px is not None:
                    header = {**header, "stop_size_px": state.stop_size_px}
            reply = None
            started = time.perf_counter()
            if ai:
                reply = upstream.ask(header, image)
            elif upstream.sock:
                upstream.close()          # AI turned off: let server.py end its session
            model_ms = (time.perf_counter() - started) * 1000 if ai else None

            model_motors = None
            if reply is not None:
                try:
                    model_motors = _motors_from(reply.get("motors"))
                except (TypeError, ValueError):
                    model_motors = None
            motors, reason = state.choose_motors(model_motors)
            label = reply.get("label") if reply else None
            score = reply.get("score") if reply else None
            # Reply to the car first; everything else happens afterwards.
            P.send_message(conn, {"type": "command", "seq": seq, "label": label,
                                  "score": score, "motors": motors})

            window_frames += 1
            elapsed = time.monotonic() - window_start
            if elapsed >= 1.0:
                with state.lock:
                    state.fps = window_frames / elapsed
                window_start, window_frames = time.monotonic(), 0

            meta = {k: header.get(k) for k in ("seq", "format", "width", "height")}
            last = {"seq": seq, "time": _iso(received), "ai": ai, "mode_reason": reason,
                    "label": label, "score": score, "model_motors": model_motors,
                    "box_px": reply.get("box_px") if reply else None,
                    "sent_motors": motors,
                    "model_ms": round(model_ms, 1) if model_ms is not None else None,
                    "round_trip_ms": header.get("last_rtt_ms")}
            info = {k: reply.get(k) for k in ("stop_size_px", "px_to_inches") if reply and reply.get(k)}
            if info:
                with state.lock:
                    state.server_info.update(info)  # kept, so the page still shows them when AI is off
            state.publish(image, meta, last)
            log.debug("frame %s: %s | %s %s", seq, reason, label, motors)
    finally:
        upstream.close()
        with state.lock:
            state.car = None
            state.fps = 0.0
        log.info("car %s:%s disconnected", *addr[:2])


def serve_car(cfg: dict, password: str, state: State) -> None:
    busy = threading.Lock()

    def run(conn: socket.socket, addr: tuple) -> None:
        try:
            handle_car(conn, addr, cfg, password, state)
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

    host, port = cfg["car_listen"]["host"], int(cfg["car_listen"]["port"])
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(2)
        log.info("waiting for the car on %s:%d (set the Pi's server.port to %d)",
                 host, port, port)
        while True:
            conn, addr = srv.accept()
            P.configure_socket(conn)
            if not busy.acquire(blocking=False):
                log.warning("refused %s: another car is already connected", addr[0])
                P.send_error(conn, "busy", "another car is already connected")
                conn.close()
                continue
            threading.Thread(target=run, args=(conn, addr), name="car", daemon=True).start()


# ==========================================================================
# Web page
# ==========================================================================

def make_handler(state: State):
    page = (UI_DIR / "page.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = "car_ui"

        def log_message(self, fmt, *args):  # keep the console for car events
            log.debug("web %s: " + fmt, self.address_string(), *args)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200) -> None:
            self._send(code, json.dumps(data).encode(), "application/json")

        # ---- GET ---------------------------------------------------------
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                self._send(200, page, "text/html; charset=utf-8")
            elif path == "/api/state":
                self._json(state.status())
            elif path == "/frame.jpg":
                with state.lock:
                    image, fmt = state.frame, state.frame_meta.get("format")
                if image is None or fmt != "jpeg":
                    self._send(404, b"no frame", "text/plain")
                else:
                    self._send(200, image, "image/jpeg")
            elif path == "/stream.mjpg":
                self._stream()
            elif path == "/api/snapshots":
                self._json(self._recent_snapshots())
            elif path.startswith("/files/"):
                self._file(unquote(path[len("/files/"):]))
            else:
                self._send(404, b"not found", "text/plain")

        def _stream(self) -> None:
            """Live view: a never-ending multipart response of JPEG frames (MJPEG)."""
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seen = -1
            try:
                while True:
                    with state.new_frame:
                        state.new_frame.wait_for(lambda: state.frame_id != seen, timeout=5.0)
                        image, fmt, fid = state.frame, state.frame_meta.get("format"), state.frame_id
                    if fid == seen or image is None or fmt != "jpeg":
                        continue
                    seen = fid
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(image)).encode() + b"\r\n\r\n" + image + b"\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def _recent_snapshots(self) -> list:
            root = state.snapshots_root
            if not root.exists():
                return []
            files = sorted((p for p in root.glob("*/*") if p.suffix in (".jpg", ".png")),
                           key=lambda p: (p.parent.name, p.name), reverse=True)
            return [str(p.relative_to(root)) for p in files[:12]]  # served under /files/

        def _file(self, rel: str) -> None:
            """Serve a saved picture. Only files inside the snapshots folder."""
            root = state.snapshots_root.resolve()
            target = (root / rel).resolve()
            if root not in target.parents or not target.is_file():
                self._send(404, b"not found", "text/plain")
                return
            ctype = {".jpg": "image/jpeg", ".png": "image/png"}.get(target.suffix,
                                                                   "application/octet-stream")
            self._send(200, target.read_bytes(), ctype)

        # ---- POST (no password: the page trusts everyone on the network) ---
        def do_POST(self):
            path = urlparse(self.path).path
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except (ValueError, json.JSONDecodeError):
                self._json({"error": "body must be JSON"}, 400)
                return
            if not isinstance(body, dict):
                self._json({"error": "body must be a JSON object"}, 400)
                return
            try:
                self._json(self._action(path, body))
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
            except OSError as exc:
                self._json({"error": f"could not save: {exc}"}, 500)

        def _action(self, path: str, body: dict) -> dict:
            if path == "/api/stop":                    # emergency stop
                with state.lock:
                    state.mode = "hold"
                    state.manual = dict(STOP)
                log.info("page: EMERGENCY STOP -> hold")
                return {"mode": "hold"}
            if path == "/api/mode":
                mode = body.get("mode")
                if mode not in MODES:
                    raise ValueError(f"mode must be one of {MODES}")
                with state.lock:
                    state.mode = mode
                    state.manual = dict(STOP)
                log.info("page: mode -> %s", mode)
                return {"mode": mode}
            if path == "/api/ai":
                with state.lock:
                    state.ai = bool(body.get("enabled"))
                    ai = state.ai
                log.info("page: AI %s", "on" if ai else "off")
                return {"ai": ai}
            if path == "/api/stop_size":               # stop sign size that means "close"
                px = max(5, min(240, int(body.get("px", ""))))  # bad value -> ValueError -> 400
                with state.lock:
                    state.stop_size_px = px
                log.info("page: stop at sign size >= %d px", px)
                return {"stop_size_px": px}
            if path == "/api/manual":                  # also the page's heartbeat
                motors = _motors_from(body.get("motors", STOP))
                with state.lock:
                    state.manual = motors
                    state.manual_at = time.monotonic()
                return {"ok": True}
            if path == "/api/snapshot":
                return state.snapshot(body.get("name", ""))
            if path == "/api/record":
                if body.get("action") == "start":
                    return state.start_recording(body.get("name", ""))
                return state.stop_recording()
            raise ValueError(f"unknown action {path}")

    return Handler


def _lan_ip() -> str:
    """Best guess at this machine's address on the network (no packets are sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "<laptop-ip>"


def _password(cfg: dict) -> str:
    """The shared car-link password: CARLINK_PASSWORD, else this config's
    "password", else the one in server.py's config file (so it's set in one place)."""
    if cfg["password"]:
        return P.resolve_password(cfg["password"])
    path = UI_DIR / cfg["server_config"]
    try:
        configured = json.loads(path.read_text(encoding="utf-8")).get("password", "")
    except FileNotFoundError:
        configured = ""
        log.warning("%s not found; set \"password\" in car_ui_config.json", path)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"can't read the password from {path}: {exc}") from None
    return P.resolve_password(configured or P.PLACEHOLDER_PASSWORD)


def main() -> None:
    parser = argparse.ArgumentParser(description="Web UI between the car and server.py")
    parser.add_argument("--config", default=str(UI_DIR / "car_ui_config.json"))
    parser.add_argument("--verbose", action="store_true", help="log every frame")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    cfg = P.load_config(args.config if Path(args.config).exists() else None, DEFAULTS)
    password = _password(cfg)
    state = State(cfg)

    web = ThreadingHTTPServer((cfg["web"]["host"], int(cfg["web"]["port"])),
                              make_handler(state))
    web.daemon_threads = True
    threading.Thread(target=web.serve_forever, name="web", daemon=True).start()
    port = int(cfg["web"]["port"])
    log.info("web UI: http://localhost:%d  (from other devices: http://%s:%d)",
             port, _lan_ip(), port)
    log.info("forwarding to server.py at %s:%s; mode %s, AI %s", cfg["server"]["host"],
             cfg["server"]["port"], state.mode, "on" if state.ai else "off")
    try:
        serve_car(cfg, password, state)
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        state.stop_recording()


if __name__ == "__main__":
    main()
