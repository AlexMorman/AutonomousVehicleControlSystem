# server/pipeline/: the image pipeline

The image pipeline (cropping, CNN, defenses, driving decisions) is a Python
package in this folder. The rest of the system treats it as a black box and
reaches it only through `server/hookup.py`, whose header holds the full
contract. The server uses it with this config:

```json
"decider": {"backend": "python", "target": "pipeline:decide"}
```

## What's here now

| File | From | What it does |
|---|---|---|
| `sign_cnn.py` | Black box team, unchanged | `SignCNN().predict(frame) -> (class name or None, confidence)`: a YOLO detector finds the most confident sign box, and a CNN classifies the crop into one of 55 LISA classes |
| `sign_detector.pt` | Black box team; training metadata removed | The detector (Ultralytics YOLO26 nano, AGPL-3.0) |
| `lisa_cnn.pt` | Black box team, unchanged | The classifier's weights and class names |
| `__init__.py` | Glue | Loads `SignCNN` once at startup, then for each frame: decode the JPEG, `predict()`, and turn the sign into motor values with the temporary rules in `../sign_decider.py` |

The black box team hasn't supplied the sign-to-motors step or the defenses
yet. When it does, its own `decide()` replaces the glue in `__init__.py`.

**Fully local:** everything runs on the laptop's CPU, at about 80 ms per frame.
`__init__.py` sets `YOLO_OFFLINE=1` before importing Ultralytics. Without it,
Ultralytics checks for internet access when imported and sends anonymous
usage analytics after predictions.

Test the recognizer on one image, from `server/pipeline/`:

```bash
YOLO_OFFLINE=1 ~/carlink-server-venv/bin/python sign_cnn.py photo.jpg
```

## The contract, in short

- **Return value:** `decide(image_bytes, meta)` returns
  `{"motors": {"front_left": x, "front_right": x, "rear_left": x,
  "rear_right": x}}`, each value from -1 to 1. Optional fields are
  `"label"`, `"score"` and `"details"`.
- **Settings:** `configure(options)` is called once at startup with
  `decider.options` from the server config.
- **Version:** `__version__` is written to every recording's `session.json`
  as `decider_version`. It's a placeholder until the team versions its
  releases.
- **Logging:** put what the pipeline concluded about a frame in `details`.
  It goes into that frame's line in `log.jsonl`, and is printed by
  `server.py --verbose`. Save extra files, such as crops, to
  `meta["session_dir"]`.
- **Timing:** answer well inside 1 s, the car's safety timeout. `decide_ms`
  in `log.jsonl` shows the real time per frame.
