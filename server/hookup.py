"""
=============================================================================
 HOOK-UP POINT: where the image pipeline plugs in
=============================================================================

The image pipeline (cropping, CNN, defenses, driving logic) is a black box to
the rest of this system. This file is the only place it connects. The
server calls ONE function for every camera frame the car sends:

    decide(image_bytes: bytes, meta: dict) -> Decision

and sends the resulting motor values back to the car. Everything specific to
the classifier and the driving logic lives behind that function. Nothing else
in the server needs to change to connect it.

There are three ways to connect your code. Pick one with the "decider"
section of server_config.json:

  1. "backend": "python"   Your code is a Python module on the server.
     Set "target" to "module_name:function_name", for example
     "my_classifier:decide". Put the module in the same folder as server.py,
     or anywhere on PYTHONPATH. Your function gets the same two arguments as decide()
     below and returns a Decision or a dict (see "What to return").
     Load your model at module import time, not inside the function, so it
     loads once instead of on every frame.
     Optional: if the module also has a function configure(options), it is
     called once at startup with the free-form "options" dict from the
     decider section of server_config.json, so the module's own settings
     (model path, thresholds, URLs) can live there instead of in code.

  2. "backend": "http"     Your code runs as a separate service.
     Set "url" to its endpoint, for example "http://127.0.0.1:8000/decide".
     For each frame the server sends:
         POST <url>
         Content-Type: image/jpeg  (image/png, or application/octet-stream for raw)
         X-Carlink-Meta: <the meta dict below, as JSON>
         body: the image bytes
     and expects a JSON reply in the "What to return" shape. "timeout_s"
     limits how long it waits.

  3. "backend": "dummy"    No classifier. Every frame gets label "dummy",
     score 1.0 and all four motors at "dummy_throttle" (default 0, so the car
     sits still). Use this to test the car and network on their own.

You can also replace the body of decide() below with your own code directly.

What you receive
----------------
image_bytes   The image exactly as the car sent it:
                "jpeg" / "png": a complete image file
                "raw": width*height*3 bytes, 8-bit blue-green-red, rows top to bottom
              decode_image() below turns any of them into a numpy array.
meta          {"seq": frame number within this connection (1, 2, 3, ...),
               "format": "jpeg" | "png" | "raw",
               "width": 320, "height": 240, "channels": 3,
               "capture_time": seconds since 1970 on the Pi's clock,
               "received_time": seconds since 1970 on the server's clock,
               "session": name of this connection's recording folder,
               "session_dir": full path of that folder (None if recording is
                              off); your code may save its own debug files
                              there, e.g. crops}
              seq 1 means a new connection: reset any per-drive state.

What to return
--------------
A Decision, or a dict with the same fields:

    {"label": "left_turn",           # any short text; saved with the frame
     "score": 0.93,                  # a number, for example the classifier's confidence
     "motors": {"front_left": 0.2,   # each from -1 (full reverse) to 1 (full forward)
                "front_right": 0.8,
                "rear_left": 0.2,
                "rear_right": 0.8}}

Optionally add "details": a dict of anything worth recording about this
frame (detections, crops' coordinates, which defense fired, timings). It is
written to that frame's line in log.jsonl and never sent to the car. Anything
not JSON-serializable is converted to text; over 64 KB it is replaced by a
note. Problems with "details" never stop the car.

All four motors are required. Values outside -1..1 are clipped. The car
applies max/min throttle limits itself, so 1.0 means "as fast as the car is
configured to go", not necessarily full battery power.

If decide() raises an exception, returns something malformed, or (for the
http backend) times out, the server sends a stop command (all motors 0) for
that frame, records the error, and carries on with the next frame.

Timing
------
The car waits for each answer before sending the next frame, so the time
decide() takes is directly the car's reaction time. If no answer arrives
within the car's safety timeout (motors.safety_timeout_s, default 1 s), the
car stops its motors until one does. If none arrives within its reply
timeout (server.reply_timeout_s, default 5 s), it disconnects and
reconnects. Aim to answer well inside 1 s. Each frame's time is logged as
decide_ms in log.jsonl.
=============================================================================
"""

from __future__ import annotations

import importlib
import json
import logging
import math
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# protocol.py lives in ../shared. server.py adds it too; this line lets other
# code (tests, the image pipeline's own scripts) import hookup directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
import protocol as P  # noqa: E402

log = logging.getLogger("hookup")


MAX_DETAILS_BYTES = 64 * 1024  # larger "details" are replaced by a note in the log


@dataclass
class Decision:
    label: str
    score: float
    motors: dict = field(default_factory=lambda: {name: 0.0 for name in P.MOTOR_NAMES})
    details: dict = field(default_factory=dict)  # recorded in log.jsonl, never sent to the car

    @classmethod
    def stop(cls, label: str = "stop", score: float = 0.0) -> "Decision":
        return cls(label=label, score=score)


def decide(image_bytes: bytes, meta: dict) -> Decision:
    """THE HOOK-UP FUNCTION. Called once per camera frame. See the top of this file."""
    if _backend is None:
        raise RuntimeError("hookup.configure() has not been called")
    return _backend(image_bytes, meta)


# --------------------------------------------------------------------------
# Helpers your code may use
# --------------------------------------------------------------------------

def decode_image(image_bytes: bytes, meta: dict):
    """Return the frame as a numpy array of shape (height, width, 3), BGR order.

    Needs numpy and OpenCV on the server: pip install numpy opencv-python-headless
    """
    import numpy as np
    if meta["format"] == "raw":
        return np.frombuffer(image_bytes, dtype=np.uint8).reshape(
            meta["height"], meta["width"], meta.get("channels", 3))
    import cv2
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"could not decode {meta['format']} image")
    return image


# --------------------------------------------------------------------------
# Backends (selected by the "decider" section of server_config.json)
# --------------------------------------------------------------------------

_backend = None
_CONTENT_TYPES = {"jpeg": "image/jpeg", "png": "image/png", "raw": "application/octet-stream"}


def configure(cfg: dict) -> str:
    """Set up the backend named in the config. Returns a description for the log."""
    global _backend
    kind = cfg.get("backend", "dummy")

    if kind == "dummy":
        throttle = float(cfg.get("dummy_throttle", 0.0))
        def _dummy(image_bytes: bytes, meta: dict) -> Decision:
            return Decision("dummy", 1.0, {name: throttle for name in P.MOTOR_NAMES})
        _backend = _dummy
        return f"dummy (all motors {throttle:+.2f})"

    if kind == "python":
        target = cfg.get("target", "")
        if ":" not in target:
            raise SystemExit("decider.target must look like 'module_name:function_name'")
        module_name, func_name = target.split(":", 1)
        module = importlib.import_module(module_name)
        func = getattr(module, func_name, None)
        if not callable(func):
            raise SystemExit(f"{module_name} has no function called {func_name!r}")
        setup = getattr(module, "configure", None)
        if callable(setup):  # optional: hand the module its own settings once, at startup
            setup(dict(cfg.get("options") or {}))
        _backend = func
        return f"python function {target}"

    if kind == "http":
        url = cfg.get("url", "")
        if not url.startswith(("http://", "https://")):
            raise SystemExit("decider.url must start with http:// or https://")
        timeout = float(cfg.get("timeout_s", 2.0))
        def _http(image_bytes: bytes, meta: dict) -> dict:
            request = urllib.request.Request(url, data=image_bytes, method="POST", headers={
                "Content-Type": _CONTENT_TYPES.get(meta["format"], "application/octet-stream"),
                "X-Carlink-Meta": json.dumps(meta)})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        _backend = _http
        return f"http service at {url} (timeout {timeout:g} s)"

    raise SystemExit(f"unknown decider.backend {kind!r}; use dummy, python or http")


def normalize(result) -> Decision:
    """Check whatever decide() returned and turn it into a clean Decision.

    Raises ValueError for anything that can't safely be sent to the car.
    """
    if isinstance(result, Decision):
        label, score, motors, details = result.label, result.score, result.motors, result.details
    elif isinstance(result, dict):
        label, score, motors, details = (result.get("label"), result.get("score"),
                                         result.get("motors"), result.get("details"))
    else:
        raise ValueError(f"decide() returned {type(result).__name__}; expected a Decision or dict")

    if not isinstance(motors, dict):
        raise ValueError("'motors' must be a dict with front_left, front_right, rear_left, rear_right")
    missing = [name for name in P.MOTOR_NAMES if name not in motors]
    if missing:
        raise ValueError(f"'motors' is missing {', '.join(missing)}")
    clean = {}
    for name in P.MOTOR_NAMES:
        try:
            value = float(motors[name])
        except (TypeError, ValueError):
            raise ValueError(f"motor {name} is {motors[name]!r}, not a number") from None
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"motor {name} is {value}")
        clean[name] = max(-1.0, min(1.0, value))

    try:
        score = float(score) if score is not None else 0.0
    except (TypeError, ValueError):
        raise ValueError(f"score is {score!r}, not a number") from None
    if math.isnan(score) or math.isinf(score):
        score = 0.0
    return Decision(label=str(label) if label is not None else "", score=score, motors=clean,
                    details=_clean_details(details))


def _json_default(obj):
    """numpy numbers and arrays become plain numbers and lists; anything else text."""
    if hasattr(obj, "tolist"):
        try:
            return obj.tolist()
        except Exception:
            pass
    return str(obj)


def _clean_details(details) -> dict:
    """Make "details" safe to write as one JSON line. Never raises: a logging
    problem must not stop the car."""
    if details is None:
        return {}
    if not isinstance(details, dict):
        details = {"value": details}
    try:
        text = json.dumps(details, default=_json_default)
    except (TypeError, ValueError) as exc:
        return {"_unloggable": f"{type(exc).__name__}: {exc}"}
    if len(text) > MAX_DETAILS_BYTES:
        return {"_too_large_bytes": len(text)}
    return json.loads(text)
