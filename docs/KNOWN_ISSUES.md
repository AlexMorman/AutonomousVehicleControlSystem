# Known issues

Problems found while building the repository (2026-11-07), checked against
the code and the test drives so far. None of them were fixed then, because
that pass changed structure and docs only. Each one could become a GitHub
issue.

## Driving behavior

1. **CNN latency causes reconnect loops.** The remote CNN often takes
   several seconds per frame, and some calls time out. The Pi drops the
   connection after `reply_timeout_s` (5 s), so many sessions end after one
   frame or none. Causes: the CNN is a remote internet service, and
   `cnn_api_client.py` opens a new HTTPS connection for every frame.
   Fixes: a locally hosted CNN (planned), and in the meantime a single
   `requests.Session()` in `cnn_api_client.py`.

2. **The timeouts don't line up.**
   - `cnn_api_client.py` uses `timeout=5`, which requests applies to the
     connect and to each read separately, so one call can take longer than
     5 s.
   - The Pi gives up after `reply_timeout_s` = 5 s.
   - `decider.timeout_s` in `server_config.json` only applies to the `http`
     backend. Nothing limits how long a `python` decider takes.

   As a result, a slow CNN reply arrives after the Pi has already
   disconnected. The CNN timeout should be clearly shorter than the Pi's
   reply timeout.

3. **The safety timeout is 10 s, and the docs say 1 s.** `pi_client.py`'s
   built-in default and `pi_config.json` were both changed to 10.0. The
   docstrings in `pi_client.py` and `hookup.py` and the original README
   still say 1 s. In practice the 5 s reply timeout disconnects first, which
   stops the motors, so the car can run for up to about 5 s on an old command.
   Once latency is fixed, consider restoring a 1 s default in code and
   setting a longer value only in the config.

4. **Most CNN classes have no action.** Only `stop` is in `ACTIONS` in
   `sign_decider.py`. Anything else gets `UNKNOWN_SIGN`, which is
   STOP. The full class list is needed from the CNN's owner.

5. **False positives and missed signs.** The CNN sometimes reports a sign
   with high confidence where none is visible, which `MIN_CONFIDENCE` = 0.5
   doesn't filter out, and it misses distant signs. `HOLD_SECONDS` keeps a false positive in effect for an extra second.
   320x240 may be too small for distant signs.

6. **With no sign in view, the car drives at full speed** (`NO_SIGN =
   FORWARD = (1.0, 1.0)`, which the Pi turns into `max_throttle` 0.6). This
   was a deliberate choice. Combined with seconds of latency, though, the car
   can travel a long way before it reacts.

## Code and tooling

7. **`wheel_test.py` doesn't read `pi_config.json`.** The wiring and reverse
   flags have to be repeated on the command line. Its docstring example
   `--reverse 2 3` is out of date: the car now reverses M1 and M4.
8. **A missing config file is silently ignored.** If `pi_config.json` or
   `server_config.json` doesn't exist, the scripts run on built-in defaults
   (server host 192.168.1.50, placeholder password) without saying so.
9. **`hookup.py` can only be imported via `server.py`**, which adds `shared/`
   to the import path. Tests or scripts that import `hookup` directly need to
   add `shared/` themselves.
10. **No automated tests.** The full loop runs with no hardware
    (`camera.device = "test"`, `motors.driver = "dry-run"`, dummy decider). That
    makes a good first pytest end-to-end test.
11. **Dependency versions aren't pinned.** Record the working versions from
    each machine with `pip freeze`, or with `apt list --installed` for the apt
    packages.
12. **Unverified:** whether the Pi actually uses `~/carlink-venv`, which the
    docs and error messages assume.

## Operations

13. **The laptop's IP changes**, which means editing `pi_config.json` every
    session. The Pi is already reached as `<pi-hostname>.local`. Giving the laptop
    a fixed `.local` hostname and putting that in `server.host` would remove
    the step.
14. **Recordings are never cleaned up**, and a connection that sends no frames
    still creates an empty session folder.
15. **Traffic is not encrypted, and only one car can connect at a time.** Both
    are accepted limits for the proof of concept.
16. **The Pi's and server's clocks aren't synchronized**, so comparing
    `capture_time` against `received_time` is meaningless.

## Planned

17. **A locally hosted CNN.** The cleanest route is to keep
    `sign_decider.py`'s use of `classify(frame) -> (class_name | None,
    confidence)` and change only `cnn_api_client.py` to call the local
    model or service. Another option is to have the local service answer in
    the `hookup.py` HTTP format and use the `http` backend directly.
