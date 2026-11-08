"""TEMPORARY: connects the sign classifier (cnn_api_client.py) to the server.

Stands in for the real image pipeline until that plugs into hookup.py.

For every frame: run classify() -> look up the action for the detected sign ->
send the motor values to the car.

Setup
    1. Put this file and cnn_api_client.py in the same folder as server.py.
    2. On the server:  pip install requests numpy opencv-python-headless
    3. In server_config.json:
           "decider": {"backend": "python", "target": "sign_decider:decide",
                       "options": {"cnn_api_url": "<the CNN API's URL>"}}

EDIT THE ACTIONS TABLE BELOW: its keys must match the class names the
classifier returns, exactly. Any sign not listed gets UNKNOWN_SIGN.

What gets recorded: "label" and "score" are what the classifier actually
returned for that frame ("none" and 0.0 if it saw no sign). "motors" is the
action that was sent. "details" says why: which sign the car acted on and
whether it was a fresh detection, a held one, unknown, or no sign.
"""

import time

import cnn_api_client
import hookup


def configure(options):
    """Called once by hookup.configure() with decider.options from server_config.json."""
    cnn_api_client.configure(options.get("cnn_api_url", ""))


# Actions: (left-side value, right-side value), each from -1 to 1.
# The car converts these to motor power using its min/max_throttle settings.
FORWARD = (1.0, 1.0)
STOP = (0.0, 0.0)
LEFT = (0.2, 0.6)    # arc left: left wheels slow, right wheels fast
RIGHT = (0.6, 0.2)

ACTIONS = {
    # "class name exactly as the classifier returns it": action
    # PLACEHOLDERS: replace with the real class names.
    "stop": STOP,
    "turn_left": LEFT,
    "turn_right": RIGHT,
    "go": FORWARD,
}

NO_SIGN = FORWARD       # nothing detected: full speed ahead. Set to STOP to wait for a sign instead.
UNKNOWN_SIGN = STOP     # detected a class that isn't in ACTIONS
MIN_CONFIDENCE = 0.5    # weaker detections are treated as no sign
HOLD_SECONDS = 1.0      # keep obeying the last sign this long after it disappears (0 = off)

_last_sign = None
_last_seen = 0.0


def decide(image_bytes, meta):
    global _last_sign, _last_seen
    if meta["seq"] == 1:  # new connection: forget the previous drive
        _last_sign, _last_seen = None, 0.0

    sign, confidence = cnn_api_client.classify(hookup.decode_image(image_bytes, meta))
    now = time.monotonic()

    if sign is not None and confidence >= MIN_CONFIDENCE:
        _last_sign, _last_seen = sign, now
        acting_on, reason = sign, "detected"
    elif _last_sign is not None and now - _last_seen < HOLD_SECONDS:
        acting_on, reason = _last_sign, "held"
    else:
        acting_on, reason = None, "no_sign"
    if acting_on is None:
        action = NO_SIGN
    elif acting_on in ACTIONS:
        action = ACTIONS[acting_on]
    else:
        action, reason = UNKNOWN_SIGN, reason + "_unknown_class"

    left, right = action
    return {"label": sign if sign is not None else "none",
            "score": confidence,
            "motors": {"front_left": left, "rear_left": left,
                       "front_right": right, "rear_right": right},
            "details": {"acting_on": acting_on, "reason": reason}}
