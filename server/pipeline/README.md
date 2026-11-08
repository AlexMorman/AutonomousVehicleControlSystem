# server/pipeline/: the image pipeline goes here

The image pipeline (cropping, CNN, defenses, driving decisions) lives in this
folder as a Python package. The rest of the system treats it as a black box
and reaches it only through `server/hookup.py`. The full contract is at the
top of that file.

Minimum layout:

```
server/pipeline/
    __init__.py      defines decide(image_bytes, meta), plus optionally
                     configure(options) and __version__
    ...              anything else the pipeline needs
```

Point the server at it in `server/server_config.json`:

```json
"decider": {
  "backend": "python",
  "target": "pipeline:decide",
  "options": {"anything": "the pipeline wants passed to configure()"}
}
```

- **Return value:** `decide()` returns
  `{"motors": {"front_left": x, "front_right": x, "rear_left": x,
  "rear_right": x}}`, each value from -1 to 1. Optional fields are
  `"label"`, `"score"` and `"details"`.
- **Version:** set `__version__ = "..."` in `__init__.py`. The server writes
  it to every recording's `session.json` as `decider_version`.
- **Logging:** put what the pipeline concluded about a frame in `details`.
  It goes into that frame's line in `log.jsonl`, and is printed by
  `server.py --verbose`. Save extra files, such as crops, to
  `meta["session_dir"]`.
- **Timing:** answer well inside 1 s, the car's safety timeout.
  `decide_ms` in `log.jsonl` shows the real time per frame.

Until the pipeline is ready, the server uses the temporary
`sign_decider:decide`.
