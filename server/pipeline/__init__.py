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

_model = None


def configure(options):
    """Called once by hookup at server startup: load both models (about 1 s)."""
    global _model
    _model = SignCNN()


def decide(image_bytes, meta):
    frame = hookup.decode_image(image_bytes, meta)
    sign, confidence = _model.predict(frame)
    return sign_decider.decide_from_sign(sign, confidence, meta)
