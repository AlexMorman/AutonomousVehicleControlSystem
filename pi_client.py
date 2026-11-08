#!/usr/bin/env python3
"""Pi side of the car link.

Loop: take the newest webcam frame -> send it to the server over TCP -> wait for
the motor command -> set the four motors -> repeat.

Safety: the motors stop if no command arrives for `motors.safety_timeout_s`
(default 1 s), if the connection drops, or if this script exits for any reason.

Usage
    python3 pi_client.py                     # normal run, uses pi_config.json
    python3 pi_client.py --test-camera       # save one frame to a file and exit
    python3 pi_client.py --test-motors       # spin each motor in turn (wheels off the ground!)
    python3 pi_client.py --dry-run           # run without the motor Bonnet (prints instead)
    python3 pi_client.py --config other.json --verbose
"""

from __future__ import annotations

import argparse
import logging
import signal
import socket
import sys
import threading
import time
from pathlib import Path

import protocol as P

log = logging.getLogger("pi")

DEFAULTS = {
    "server": {
        "host": "192.168.1.50",
        "port": P.DEFAULT_PORT,
        "password": P.PLACEHOLDER_PASSWORD,
        "connect_timeout_s": 5.0,
        "reply_timeout_s": 5.0,     # no reply this long -> drop the connection and reconnect
        "reconnect_delay_s": 2.0,
    },
    "camera": {
        "device": 0,                # 0 = /dev/video0; also accepts a path, or "test" for a fake camera
        "width": 320,
        "height": 240,
        "fps": 15,
        "format": "jpeg",           # "jpeg", "png" or "raw"
        "jpeg_quality": 80,         # 1-100, only used for "jpeg"
    },
    "motors": {
        "driver": "bonnet",         # "bonnet" or "dry-run"
        "bonnet_i2c_address": 96,   # 96 = 0x60, the Bonnet's default
        "safety_timeout_s": 10.0,
        "max_throttle": 0.6,        # power applied for a command of +/-1
        "min_throttle": 0.0,        # power applied for the smallest non-zero command
        "deadband": 0.05,           # commands smaller than this are treated as 0
        "wiring": {
            "front_left": {"terminal": 1, "reverse": False},
            "rear_left": {"terminal": 2, "reverse": False},
            "front_right": {"terminal": 3, "reverse": False},
            "rear_right": {"terminal": 4, "reverse": False},
        },
    },
    "stats_every_s": 5.0,
}


# ==========================================================================
# Camera
# ==========================================================================

class CameraError(Exception):
    pass


class Camera:
    """Reads the webcam continuously on a background thread and keeps only the
    newest frame, so the loop never acts on a stale image.

    Extra memory: one frame (about 230 KB at 320x240).
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._cond = threading.Condition()
        self._frame = None
        self._stamp = 0.0
        self._seq = 0
        self._stop = threading.Event()
        if cfg["device"] == "test":
            self._source = _TestPatternSource(cfg)
        else:
            self._source = _OpenCVSource(cfg)
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)

    def start(self) -> "Camera":
        self._thread.start()
        return self

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            ok, frame = self._source.read()
            if not ok or frame is None:
                failures += 1
                if failures in (10, 100) or failures % 1000 == 0:
                    log.warning("camera: %d failed reads in a row", failures)
                time.sleep(0.05)
                continue
            failures = 0
            with self._cond:
                self._frame = frame
                self._stamp = time.time()
                self._seq += 1
                self._cond.notify_all()

    def latest(self, newer_than: int, timeout: float = 2.0):
        """Return (seq, capture_time, frame) for a frame newer than `newer_than`."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._seq > newer_than, timeout):
                raise CameraError(f"no new frame from the camera in {timeout:.1f} s")
            return self._seq, self._stamp, self._frame

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._source.close()


class _OpenCVSource:
    def __init__(self, cfg: dict):
        import cv2
        device = cfg["device"]
        self.cap = None
        if sys.platform.startswith("linux"):
            self.cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        if self.cap is None or not self.cap.isOpened():
            self.cap = cv2.VideoCapture(device)
        if not self.cap.isOpened():
            raise CameraError(f"could not open camera {device!r}; is it plugged in? "
                              "List cameras with: v4l2-ctl --list-devices")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg["width"])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg["height"])
        self.cap.set(cv2.CAP_PROP_FPS, cfg["fps"])
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if (w, h) != (cfg["width"], cfg["height"]):
            log.info("camera delivers %dx%d; frames will be resized to %dx%d",
                     w, h, cfg["width"], cfg["height"])

    def read(self):
        return self.cap.read()

    def close(self) -> None:
        self.cap.release()


class _TestPatternSource:
    """Fake camera for testing without hardware: a moving bar plus a frame counter."""

    def __init__(self, cfg: dict):
        import numpy as np
        self.np = np
        self.w, self.h = cfg["width"], cfg["height"]
        self.period = 1.0 / max(1, cfg["fps"])
        self.n = 0

    def read(self):
        import cv2
        time.sleep(self.period)
        self.n += 1
        frame = self.np.full((self.h, self.w, 3), 40, dtype=self.np.uint8)
        x = (self.n * 4) % self.w
        frame[:, x:x + 20] = (0, 200, 255)
        cv2.putText(frame, f"test {self.n}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, (255, 255, 255), 2)
        return True, frame

    def close(self) -> None:
        pass


def encode_frame(frame, cfg: dict) -> tuple[bytes, dict]:
    """Resize to the configured size if needed, then encode in the configured format."""
    import cv2
    w, h = cfg["width"], cfg["height"]
    if frame.shape[1] != w or frame.shape[0] != h:
        frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
    fmt = cfg["format"]
    if fmt == "jpeg":
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, int(cfg["jpeg_quality"])])
        data = buf.tobytes() if ok else None
    elif fmt == "png":
        ok, buf = cv2.imencode(".png", frame)
        data = buf.tobytes() if ok else None
    else:  # raw
        data = frame.tobytes()
    if data is None:
        raise CameraError(f"could not encode frame as {fmt}")
    meta = {"format": fmt, "width": w, "height": h, "channels": 3}
    if fmt == "raw":
        meta["pixel_format"] = "bgr24"  # 8-bit blue, green, red; rows top to bottom
    return data, meta


# ==========================================================================
# Motors
# ==========================================================================

class _BonnetDriver:
    def __init__(self, address: int):
        try:
            from adafruit_motorkit import MotorKit
        except ImportError:
            raise SystemExit("adafruit-circuitpython-motorkit is not installed; see README, "
                             "or use --dry-run to test without the Bonnet") from None
        try:
            self.kit = MotorKit(address=address)
        except (ValueError, OSError) as exc:
            raise SystemExit(f"could not reach the motor Bonnet at I2C address "
                             f"0x{address:02x}: {exc}\nIs I2C enabled, and does "
                             "'i2cdetect -y 1' show the Bonnet?") from None

    def set(self, terminal: int, throttle: float) -> None:
        getattr(self.kit, f"motor{terminal}").throttle = throttle


class _DryRunDriver:
    def __init__(self):
        self.values = {}

    def set(self, terminal: int, throttle: float) -> None:
        if self.values.get(terminal) != throttle:
            log.debug("dry-run: M%d -> %+.2f", terminal, throttle)
        self.values[terminal] = throttle


class MotorController:
    """Maps the server's commands onto the four motors, and stops them if
    commands stop arriving (the safety stop runs on its own small thread, so
    it works even if the network code hangs)."""

    def __init__(self, cfg: dict, dry_run: bool = False):
        self.cfg = cfg
        if dry_run or cfg["driver"] == "dry-run":
            self.driver = _DryRunDriver()
            log.info("motors: dry run (no Bonnet)")
        else:
            self.driver = _BonnetDriver(int(cfg["bonnet_i2c_address"]))
        self.wiring = cfg["wiring"]
        terminals = [self.wiring[name]["terminal"] for name in P.MOTOR_NAMES]
        if sorted(terminals) != [1, 2, 3, 4]:
            raise SystemExit("motors.wiring must assign terminals 1, 2, 3 and 4 exactly once")
        self._lock = threading.Lock()
        self._last_command = time.monotonic()
        self._moving = False
        self._stop_event = threading.Event()
        self._watchdog = threading.Thread(target=self._watch, name="safety", daemon=True)
        self.stop("startup")
        self._watchdog.start()

    def _to_throttle(self, value) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return 0.0
        if v != v:  # NaN
            return 0.0
        v = max(-1.0, min(1.0, v))
        if abs(v) < self.cfg["deadband"]:
            return 0.0
        lo, hi = float(self.cfg["min_throttle"]), float(self.cfg["max_throttle"])
        magnitude = lo + abs(v) * (hi - lo)
        return round(magnitude if v > 0 else -magnitude, 4)

    def apply(self, motors: dict) -> dict:
        """Set all four motors from a command. Missing motors are set to 0."""
        applied = {}
        with self._lock:
            for name in P.MOTOR_NAMES:
                throttle = self._to_throttle(motors.get(name, 0.0))
                if self.wiring[name].get("reverse"):
                    throttle = -throttle
                self.driver.set(self.wiring[name]["terminal"], throttle)
                applied[name] = throttle
            self._last_command = time.monotonic()
            self._moving = any(applied.values())
        return applied

    def stop(self, reason: str = "") -> None:
        with self._lock:
            was_moving = self._moving
            for name in P.MOTOR_NAMES:
                try:
                    self.driver.set(self.wiring[name]["terminal"], 0.0)
                except Exception as exc:  # keep trying the other motors
                    log.error("could not stop %s: %s", name, exc)
            self._moving = False
        if was_moving and reason:
            log.warning("motors stopped: %s", reason)

    def _watch(self) -> None:
        timeout = float(self.cfg["safety_timeout_s"])
        while not self._stop_event.wait(0.05):
            if self._moving and time.monotonic() - self._last_command > timeout:
                self.stop(f"no command for {timeout:.1f} s (safety timeout)")

    def close(self) -> None:
        self._stop_event.set()
        self.stop("shutting down")


# ==========================================================================
# Network loop
# ==========================================================================

class Stats:
    def __init__(self, every_s: float):
        self.every_s = every_s
        self.reset()

    def reset(self) -> None:
        self.t0 = time.monotonic()
        self.frames = 0
        self.rtt_total = 0.0
        self.bytes = 0

    def add(self, rtt_s: float, nbytes: int) -> None:
        self.frames += 1
        self.rtt_total += rtt_s
        self.bytes += nbytes
        elapsed = time.monotonic() - self.t0
        if self.every_s and elapsed >= self.every_s:
            log.info("%.1f frames/s, average round trip %.0f ms, average image %.1f KB",
                     self.frames / elapsed, 1000 * self.rtt_total / self.frames,
                     self.bytes / self.frames / 1024)
            self.reset()


def run_session(cfg: dict, password: str, camera: Camera, motors: MotorController) -> None:
    """Connect, authenticate and run the frame/command loop until something fails."""
    srv = cfg["server"]
    log.info("connecting to %s:%s ...", srv["host"], srv["port"])
    sock = socket.create_connection((srv["host"], int(srv["port"])),
                                    timeout=float(srv["connect_timeout_s"]))
    try:
        P.configure_socket(sock)
        sock.settimeout(float(srv["reply_timeout_s"]))
        P.client_handshake(sock, password)
        log.info("connected and authenticated")
        stats = Stats(float(cfg["stats_every_s"]))
        last_cam_seq = 0
        seq = 0
        last_rtt_ms = None
        while True:
            last_cam_seq, captured, frame = camera.latest(newer_than=last_cam_seq)
            data, meta = encode_frame(frame, cfg["camera"])
            seq += 1
            header = {"type": "frame", "seq": seq, "capture_time": captured,
                      "last_rtt_ms": last_rtt_ms, **meta}
            sent = time.monotonic()
            P.send_message(sock, header, data)
            while True:  # in lockstep the reply always matches, but be defensive
                reply, _ = P.recv_message(sock, max_payload=0)
                if reply.get("type") == "command" and reply.get("seq") == seq:
                    break
                log.warning("ignoring unexpected message: %s", reply)
            rtt = time.monotonic() - sent
            last_rtt_ms = round(rtt * 1000, 1)
            motors.apply(reply.get("motors") or {})
            log.debug("seq %d: %s (%.2f) %s, %.0f ms", seq, reply.get("label"),
                      reply.get("score") or 0.0, reply.get("motors"), rtt * 1000)
            stats.add(rtt, len(data))
    finally:
        motors.stop("connection ended")
        sock.close()


def run_forever(cfg: dict, password: str, camera: Camera, motors: MotorController) -> None:
    delay = float(cfg["server"]["reconnect_delay_s"])
    while True:
        try:
            run_session(cfg, password, camera, motors)
        except P.AuthError as exc:
            raise SystemExit(f"{exc}. Check the password on both machines.") from None
        except P.PeerError as exc:
            if exc.reason == "busy":
                log.warning("server is busy with another car; retrying in %.0f s", delay)
            else:
                log.error("server reported an error: %s", exc)
        except socket.timeout:
            log.error("no reply from the server within %.1f s; reconnecting",
                      float(cfg["server"]["reply_timeout_s"]))
        except (OSError, P.ProtocolError) as exc:
            log.error("connection problem: %s", exc)
        except CameraError as exc:
            log.error("camera problem: %s", exc)
        time.sleep(delay)


# ==========================================================================
# Test modes
# ==========================================================================

def test_camera(cfg: dict) -> None:
    camera = Camera(cfg["camera"]).start()
    try:
        _, _, frame = camera.latest(newer_than=0, timeout=5.0)
        data, meta = encode_frame(frame, cfg["camera"])
    finally:
        camera.close()
    out = Path("camera_test" + P.FILE_EXTENSIONS[meta["format"]])
    out.write_bytes(data)
    log.info("saved %s: %dx%d %s, %.1f KB", out, meta["width"], meta["height"],
             meta["format"], len(data) / 1024)


def test_motors(cfg: dict, dry_run: bool, throttle: float) -> None:
    motors = MotorController(cfg["motors"], dry_run=dry_run)
    try:
        for name in P.MOTOR_NAMES:
            power = abs(motors.apply({name: throttle})[name])
            log.info("%s forward: command %.2f = power %.2f (2 s) ...", name, throttle, power)
            end = time.monotonic() + 2.0
            while time.monotonic() < end:  # re-send so the safety stop doesn't trip
                motors.apply({name: throttle})
                time.sleep(0.1)
            motors.stop()
            time.sleep(0.7)
        log.info("done. Each wheel should have spun forward, in the order above. "
                 "Fix a wrong order with 'terminal' and a wrong direction with 'reverse'.")
    finally:
        motors.close()


# ==========================================================================
# Entry point
# ==========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Pi side of the car link")
    parser.add_argument("--config", default=str(Path(__file__).with_name("pi_config.json")))
    parser.add_argument("--dry-run", action="store_true", help="don't touch the motor Bonnet")
    parser.add_argument("--test-camera", action="store_true", help="save one frame and exit")
    parser.add_argument("--test-motors", action="store_true",
                        help="spin each motor in turn and exit (wheels off the ground!)")
    parser.add_argument("--throttle", type=float, default=0.5,
                        help="command value for --test-motors (default 0.5)")
    parser.add_argument("--verbose", action="store_true", help="log every frame")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    cfg = P.load_config(args.config if Path(args.config).exists() else None, DEFAULTS)
    if cfg["camera"]["format"] not in P.IMAGE_FORMATS:
        raise SystemExit(f"camera.format must be one of {P.IMAGE_FORMATS}")

    # Turn "kill" / systemd stop into a normal exit so the motors get stopped.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    if args.test_camera:
        return test_camera(cfg)
    if args.test_motors:
        return test_motors(cfg, args.dry_run, args.throttle)

    password = P.resolve_password(cfg["server"]["password"])
    motors = MotorController(cfg["motors"], dry_run=args.dry_run)
    camera = None
    try:
        camera = Camera(cfg["camera"]).start()
        run_forever(cfg, password, camera, motors)
    except KeyboardInterrupt:
        log.info("stopping")
    except CameraError as exc:
        raise SystemExit(f"camera problem: {exc}") from None
    finally:
        motors.close()
        if camera is not None:
            camera.close()


if __name__ == "__main__":
    main()
