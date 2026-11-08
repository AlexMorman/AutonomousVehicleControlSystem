# Known issues

Open problems, checked against the code and the test drives so far. Each
one could become a GitHub issue. Last updated 2026-11-07.

The image pipeline (cropping, CNN, defenses, driving logic) is a black box
that plugs into `server/hookup.py`. Section 1 is about that connection.
Section 2 covers the temporary stand-in (`sign_decider.py` +
`cnn_api_client.py`) and goes away when the pipeline arrives.

## 1. Connecting the image pipeline

1. **A `decide()` that hangs locks the car out until the server is
   restarted.** The server calls `decide()` on the connection's own thread
   and only frees its one-car slot when that thread finishes. If `decide()`
   never returns, the Pi times out after 5 s and reconnects, and from then
   on the server refuses it as `busy`. A `decide()` that is only slow, but
   does return, recovers on its own. Fix: run `decide()` on a worker thread
   with a deadline, and send a stop command when the deadline passes.

2. **Nothing limits how long `decide()` takes.** `decider.timeout_s` only
   applies to the `http` backend. With the 1 s safety timeout, any frame
   slower than about 1 s makes the car stop and start, and anything over 5 s
   causes a reconnect. On the laptop's CPU (no GPU), a CNN plus cropping
   plus defenses could easily take that long. `decide_ms` in `log.jsonl`
   shows the real time per frame. Watch it first; fix 1 would also enforce
   a limit here.

3. **The pipeline runs inside the server process.** Python exceptions are
   handled (the car gets a stop command and the error is logged). A crash
   in native code (OpenCV, onnxruntime, torch) or running out of memory
   takes the whole server down, and the car stops within 1 s. If that
   happens, run the pipeline as a separate local service and use the `http`
   backend, which already exists.

4. **Reaction time equals pipeline time.** The Pi sends one frame and waits
   for the answer, so between answers the car drives on the last command
   without seeing. This is acceptable for the proof of concept; changing it
   means changing the protocol.

5. **The pipeline isn't told when a drive starts or ends.** It has to infer
   a new drive from `meta["seq"] == 1`, and nothing tells it a connection
   closed. If the pipeline keeps history (tracking, defenses), consider
   optional `on_session_start` / `on_session_end` hooks in `hookup.py`.

6. **The `http` backend has only been tested against a mock service.** It
   passes `details` through, but not `decider.options`. Those would have
   to go in the service's own config.

## 2. Temporary CNN stand-in (until the pipeline replaces it)

7. **CNN latency causes reconnect loops.** The remote CNN often takes
   several seconds per frame, and some calls time out, so many sessions end
   after one frame or none. The API is on the
   internet, and `cnn_api_client.py` opens a new HTTPS connection for every
   frame; a single `requests.Session()` would help. **With the safety
   timeout back to 1 s, the car will stop and start on every frame while
   using this path.** Set `motors.safety_timeout_s` higher in your own
   `pi_config.json` while testing with it.
8. **The timeouts don't line up.** `requests` applies `timeout=5` to the
   connect and to each read separately, so one call can take longer than
   5 s, longer than the Pi's 5 s reply timeout.
9. **Only `stop` has an action.** Any other class gets `UNKNOWN_SIGN` (stop). The `details` field now records this as
   `detected_unknown_class`.
10. **False positives and missed distant signs.** `HOLD_SECONDS` keeps a
    false positive in effect for another second.
11. **No sign means full speed** (`NO_SIGN = FORWARD`). This was a
    deliberate choice, but risky combined with seconds of latency.
12. **Each frame is JPEG-compressed twice**: once on the Pi, and again by
    `cnn_api_client.py` before uploading. This only affects this path; see
    the README's note for the pipeline.

## 3. Car, server and operations

13. **A missing config file is silently ignored.** Without `pi_config.json`
    or `server_config.json`, the scripts run on built-in defaults (server
    host 192.168.1.50, placeholder password) and don't say so.
    `wheel_test.py` does warn.
14. **No automated tests.** The full loop runs with no hardware (test-pattern
    camera, dry-run motors, dummy decider), which makes a good first pytest
    end-to-end test, together with a fake pipeline module.
15. **The laptop's IP changes every session.** A `.local` hostname for the
    laptop would remove the config edit. See the README.
16. **Recordings are never cleaned up**, and a connection that sends no frames
    still creates an empty session folder.
17. **Traffic is not encrypted, and only one car can connect at a time.**
    Both are accepted for the proof of concept.
18. **The Pi's and server's clocks aren't synchronized**, so comparing
    `capture_time` against `received_time` is meaningless.

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
- **It was unknown whether the Pi uses `~/carlink-venv`.** Confirmed: it
  does.
- Dropped by decision (2026-11-07): camera resolution, testing models
  without the car, and laptop dependency setup (handled manually).
