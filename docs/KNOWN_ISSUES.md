# Known issues

Open problems, checked against the code and the test drives so far. Each
one could become a GitHub issue. Last updated 2026-11-08.

The image pipeline is a black box that plugs into `server/hookup.py`. It
now lives in `server/pipeline/`: the black box team's local sign
recognizer (`sign_cnn.py`), plus temporary glue that borrows the
sign-to-motors rules from `sign_decider.py`. Section 2 is about that
pipeline; section 3 only applies if you switch back to the old remote API.

## 1. Connecting the image pipeline

1. **Nothing limits how long `decide()` takes.** `decider.timeout_s` only
   applies to the `http` backend. With the 1 s safety timeout, any frame
   slower than about 1 s makes the car stop and start, and anything over 5 s
   causes a reconnect. Today's pipeline takes about 80 ms, but defenses added
   later count against the same budget. `decide_ms` in `log.jsonl` shows the
   real time per frame.

2. **Reaction time equals pipeline time.** The Pi sends one frame and waits
   for the answer, so between answers the car drives on the last command
   without seeing. This is acceptable for the proof of concept; changing it
   means changing the protocol.

3. **The `http` backend has only been tested against a mock service.** It
   passes `details` through, but not `decider.options`. Those would have
   to go in the service's own config.

## 2. Image pipeline (`server/pipeline/`)

4. **The detector's license needs a decision before the repo is public.**
   `sign_detector.pt` is a fine-tuned Ultralytics YOLO26 model under
   AGPL-3.0, and `ultralytics` itself is AGPL-3.0. The repository has no
   license. Also check the LISA dataset's terms for redistributing a model
   trained on it.
5. **The reported confidence can't tell real signs from false ones.**
   `sign_cnn.predict()` returns the classifier's confidence, which is close to
   1.0 even on false detections, and throws away the detector's. As a
   result, `MIN_CONFIDENCE` = 0.5 in `sign_decider.py` filters nothing.
   This is for the black box team to fix in `predict()`.
6. **Only one sign per frame**: the box the detector is most sure of. A
   second sign in view is never seen.
7. **Distant signs are missed.** The detector scores small, distant signs
   well under its fixed 0.42 cut-off.
8. **The sign-to-motors rules are still the temporary ones.** Only `stop`
   is mapped. Every other class gets `UNKNOWN_SIGN` (stop), and no sign
   means full speed (`NO_SIGN = FORWARD`). `HOLD_SECONDS` keeps a false
   positive in effect for another second. The team's own `decide()` will
   replace these rules.
9. **`__version__` is a placeholder** (`"sign_cnn 2026-11-06 (unversioned)"`)
   until the team versions its releases.

## 3. Old remote-API path (`target` = `sign_decider:decide`)

10. **CNN latency causes reconnect loops.** The remote CNN often takes
    several seconds per frame, and some calls time out. To use this path,
    raise `motors.safety_timeout_s` in your own `pi_config.json`.
11. **The timeouts don't line up.** `requests` applies `timeout=5` to the
    connect and to each read separately, so one call can take longer than the
    Pi's 5 s reply timeout.
12. **Each frame is JPEG-compressed twice**: on the Pi, and again by
    `cnn_api_client.py` before uploading.

## 4. Car, server and operations

13. **A missing config file is silently ignored.** Without `pi_config.json`
    or `server_config.json`, the scripts run on built-in defaults (server
    host 192.168.1.50, placeholder password) and don't say so.
    `wheel_test.py` does warn.
14. **The laptop's IP changes every session**, so `server.host` in
    `pi_config.json` has to be edited each time. A `.local` hostname was
    considered and rejected (it advertises the laptop on every network it
    joins).
15. **Recordings are never cleaned up**, and a connection that sends no frames
    still creates an empty session folder.
16. **The Pi's and server's clocks aren't synchronized**, so comparing
    `capture_time` against `received_time` is meaningless.

## Accepted (not going to fix)

Decided 2026-11-07: if the pipeline, server or car gets into a bad state, a
hard reset of that machine is acceptable.

- A `decide()` that never returns keeps the server's one-car slot, and the
  Pi is then refused as `busy` until the server restarts.
- A native crash or running out of memory in the pipeline takes the server
  down (Python exceptions are still caught, and the car gets a stop).
- **The network is out of scope.** The threat model covers only physical-world
  perturbations (for example altered signs). Traffic between the car and the
  laptop is unencrypted, and frames and commands aren't authenticated after
  the password check. That's accepted.
- Only one car can connect, and the power settings in `pi_config.json` are
  fixed for this one car.
- The Pi only prints to the console and writes nothing to disk, to avoid
  unnecessary SD-card writes. Its safety stops and reconnects are therefore
  only visible in the terminal while it runs. (The one exception is
  `pi_client.py --test-camera`, which saves a single photo when asked to.)
- No automated test suite. The two manual hardware tests in `pi/tests/` are
  enough.
- No session start/end hooks: the pipeline handles the frame sequence itself
  using `meta["seq"]` (which restarts at 1 on every reconnect) and
  `meta["capture_time"]`.

## Resolved

- **The CNN API URL was hardcoded.** It's now in `server_config.json`
  (`decider.options.cnn_api_url`), outside git.
- **The pipeline's output wasn't recorded.** It is now: the optional
  `details` field goes to `log.jsonl`, `session_dir` lets the pipeline save
  its own files, and `session.json` records the decider target.
- **`hookup.py` could only be imported through `server.py`.** It now imports
  on its own.
- **The safety timeout default was 10 s.** It's back to 1 s in code and in
  the example config.
- **`wheel_test.py` ignored the config.** It now reads the wiring, reverse
  flags and I2C address.
- **There was no home or version record for the pipeline.** It now goes in
  `server/pipeline/`, and its `__version__` is written to every
  `session.json`.
- **The CNN depended on a remote internet API**, with seconds of latency per
  frame. It now runs locally in `server/pipeline/` at about 80 ms per frame,
  with Ultralytics' network checks and analytics switched off.
- **It was unknown whether the Pi uses `~/carlink-venv`.** Confirmed: it
  does.
- Dropped by decision (2026-11-07): camera resolution, testing models
  without the car, and laptop dependency setup (handled manually).
