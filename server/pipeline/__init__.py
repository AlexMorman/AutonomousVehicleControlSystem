"""The image pipeline (black box): what hookup.py calls for every frame.

Config, in server/server_config.json:
    "decider": {"backend": "python", "target": "pipeline:decide"}

Contents today:
    sign_cnn.py, sign_detector.pt, lisa_cnn.pt
        The black box team's sign recognizer, as delivered (sign_detector.pt
        had its training metadata removed; see CHANGELOG.md). Runs locally on
        the CPU: frame -> (sign class or None, confidence).
    this file
        Glue between hookup.py and sign_cnn. The team has not supplied the
        sign-to-motors step or the defenses yet, so decide() borrows the
        temporary rules in ../sign_decider.py (decide_from_sign). Replace
        this file when the team delivers its own decide().
"""

import logging
import os

# Keep the pipeline fully local: without this, Ultralytics checks for internet
# access at import and sends anonymous usage analytics after predictions.
# Must be set before sign_cnn (and so ultralytics) is imported.
os.environ.setdefault("YOLO_OFFLINE", "1")

import hookup  # noqa: E402
import sign_decider  # noqa: E402
from .sign_cnn import SignCNN  # noqa: E402

# Written to every recording's session.json. Placeholder until the black box
# team versions its releases.
__version__ = "sign_cnn 2026-11-06 (unversioned)"

log = logging.getLogger("pipeline")

_model = None
_stop_size_px = None  # from server_config.json; the only place it is set


def configure(options):
    """Called once by hookup at server startup with decider.options: read
    stop_size_px (required), then load both models (about 1 s)."""
    global _model, _stop_size_px
    value = options.get("stop_size_px")
    if isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 240:
        raise SystemExit(
            "server_config.json: decider.options.stop_size_px must be a whole number from 5 to 240 "
            f"(got {value!r}). It is the stop sign's box height in px at which the car stops; "
            f"inches ~= {sign_decider.PX_TO_INCHES} / px, so 30 ~= 3 ft. Example: "
            '"options": {"stop_size_px": 30}')
    _stop_size_px = value
    log.info("stop sign: stop once its box is >= %d px (~%d in)",
             value, round(sign_decider.PX_TO_INCHES / value))
    _model = SignCNN()


def decide(image_bytes, meta):
    frame = hookup.decode_image(image_bytes, meta)
    sign, confidence = _model.predict(frame)
    return sign_decider.decide_from_sign(sign, confidence, meta, box_px=_model.last_box_px,
                                         stop_size_px=_stop_size_px)
