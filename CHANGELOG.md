# Changelog

## 2026-11-07, fifth round: applied motor power in the Pi's verbose output

### Show applied motor power with pi_client.py --verbose

- `pi/pi_client.py`:
  - With `--verbose`, each frame's line shows the label, score, round trip,
    the command received (`FL/FR/RL/RR`, -1 to 1) and the power applied to
    each Bonnet terminal in percent (`M1`–`M4`). The power is after
    `max_throttle`, `min_throttle`, the deadband and the reverse flags, so
    its sign is the actual direction sent to the terminal.
  - New helpers: `MotorController.format_power()` and `_format_command()`.
    The line is only built when verbose output is on.
  - The dry-run driver no longer logs its own per-motor changes, since the
    verbose line covers them. `--verbose` help text and the module docstring
    were updated.
  - Console only; nothing is written to disk.
- `README.md`: running section shows `--verbose` with an example line.
- Tested against a dummy server with the example config (max 60%, M1 and M4
  reversed). Commands of 1.0, 0.5, -1.0 and 0.03 gave M1 -60% M2 +60% M3
  +60% M4 -60%; ±30%; the mirror image of the first; and +0% everywhere
  (inside the deadband). Without `--verbose` no per-frame lines appear.


## 2026-11-07, fourth round: manual tests and the pipeline's home

Decisions recorded in this round:
- The threat model covers only physical-world perturbations, so the
  network is out of scope.
- The power settings are fixed for this one car.
- The Pi prints to the console only and writes nothing to disk.
- The example config's dummy mode runs at full speed on purpose.
- The pipeline will live in `server/pipeline/`.

### Add two manual hardware tests in pi/tests/

- `pi/tests/wheels_full_speed.py`: after a 3 s warning, spins each wheel in
  turn (front_left, front_right, rear_left, rear_right) at throttle 1.0,
  2 s each (`--seconds`). Wiring and reverse flags come from
  `pi_config.json`, so each wheel should turn forward. For each wheel it
  prints the motor name, terminal and whether the config reverses it. It
  ignores `max_throttle` on purpose. Ctrl+C and kill stop all motors.
- `pi/tests/photo_to_server.py`: takes one photo after 10 warm-up frames,
  using the configured camera settings, and sends it as a normal frame,
  with the same handshake and password as `pi_client.py`. It prints the
  photo size, the server's label, score and motor command (not applied),
  and the response time. It waits up to 15 s (`--timeout`). Wrong password,
  busy server, timeouts and connection problems each get a one-line
  message. It writes nothing on the Pi. The server records the frame as
  usual.
- Neither file name starts with `test_`, so pytest never runs them.
- Tested with a stand-in motor library (each terminal received ±1.0 in
  order, with M1 and M4 reversed) and with the test-pattern camera against
  a real server running a fake pipeline package. A normal answer, a wrong
  password and a busy server all behaved as described.

### Add server/pipeline/ and record the pipeline's version per session

- `server/pipeline/README.md`: where the pipeline goes (a package with
  `decide`, optional `configure` and `__version__`), the config to point at
  it (`"target": "pipeline:decide"`), and the logging and timing notes.
- `server/hookup.py`:
  - Stores the python decider module's `__version__`, if it has one.
    `decider_version()` returns it, and the startup log line shows it.
  - Docstring documents `__version__`.
- `server/server.py`:
  - Every `session.json` gains `decider_version`.
  - `--verbose` frame lines now include `details` when present.
- Tested: a fake `pipeline` package with `__version__ = "0.1-test"` showed
  up in the startup log and in `session.json`, and its details in the
  verbose log.

### Update docs for this round's decisions

- `README.md`:
  - Layout table lists `pi/tests/` and `server/pipeline/`.
  - New "Manual tests" section.
  - Pipeline steps point to `server/pipeline/` and mention `__version__`.
  - Configuration now states the power spec in one line: a command of 1.0
    means 60% power to motors 1–4, with motors 1 and 4 reversed.
  - The dummy backend's full-speed behavior is spelled out.
- `server/server_config.example.json`: a comment next to `dummy_throttle`
  saying 1.0 runs all wheels at full configured speed as soon as the car
  connects. The value is unchanged.
- `docs/KNOWN_ISSUES.md`:
  - The encryption/single-car and automated-tests items moved to
    "Accepted", together with the threat-model scope, fixed power settings
    and console-only Pi.
  - The pipeline's home and version recording are listed under "Resolved".
  - Items renumbered.
- `CHANGELOG.md`: this section.


## 2026-11-07, third round: removing runtime traces

### History rewritten (before the repository was first pushed)

No commit contains runtime output or details of the test environment:

- Removed from every commit: the 20 sample frames with their manifest and
  README, and the original design-conversation handoff document. The two
  commits that only added them no longer exist, and their entries were
  taken out of this changelog.
- Replaced in every commit: the laptop's IP address in
  `pi_config.example.json` and the docs became `<laptop-ip>`, and the Pi's
  username and hostname in `docs/Local_Instructions.md` became `<pi-user>`
  and `<pi-hostname>`. So the baseline's example config now holds a
  placeholder host instead of the address that was used.
- Rewritten in every version of `README.md`, `docs/KNOWN_ISSUES.md` and this
  changelog: figures and examples taken from test-drive recordings
  (latencies, session and frame counts, detected class names, specific
  frames) are now general statements. Each old commit keeps its own
  version of these files; nothing from later was copied backwards.

### Drop the .local idea, accept hard resets, document seq

- `README.md`: removed the note suggesting a `.local` hostname for the
  laptop. That approach was rejected because it advertises the laptop on
  every network it joins.
- `docs/KNOWN_ISSUES.md`:
  - The hung `decide()`, native-crash and session-hook items moved to a new
    "Accepted" section. A hard reset is fine when something gets into a
    bad state, and the pipeline handles the frame sequence itself.
  - The laptop-IP item now records that `.local` was rejected.
  - Items renumbered.
- `server/hookup.py` (docstring only): `meta["seq"]` restarts at 1 after
  every reconnect and counts frames sent, not camera frames, so the
  pipeline should use `capture_time` for timing. No code change was
  needed: `seq` was already passed to `decide()`.
- `CHANGELOG.md`: this section.


## 2026-11-07, second round: preparing for the image pipeline

The image pipeline (cropping, CNN, defenses) is a black box that returns
motor commands and plugs into `server/hookup.py`. `sign_decider.py` and
`cnn_api_client.py` are temporary stand-ins until it is ready. One commit
per entry, oldest first.

### Move the CNN API URL into server config via decider.options

- `shared/protocol.py`: a config section whose built-in default is an empty
  dict is now free-form. Its keys are taken as given (except `_` comment
  keys) instead of being warned about and dropped. This doesn't affect the
  Pi, which has no such section.
- `server/server.py`: new default `decider.options = {}`.
- `server/hookup.py`: with the `python` backend, if the target module
  defines `configure(options)`, it is called once at startup with
  `decider.options`.
- `server/cnn_api_client.py`: the hardcoded URL is gone. `configure(url)`
  sets it; the `CNN_API_URL` environment variable overrides it. Startup stops
  with a clear message if neither is set. The command line accepts `--url`,
  or reads `server_config.json`. Marked temporary.
- `server/sign_decider.py`: new `configure(options)`, which passes
  `cnn_api_url` on. Calls `cnn_api_client.classify` through the module.
  Marked temporary.
- `server/server_config.example.json`: new `decider.options.cnn_api_url`,
  empty. **Your own `server_config.json` needs this key with the real URL,
  or the server won't start with `sign_decider`.**

### Record what the decider says about each frame

- `server/hookup.py`:
  - `Decision` has a new `details` dict. `normalize()` accepts `details`
    from a Decision or a dict. A non-dict becomes `{"value": ...}`.
  - numpy numbers and arrays become plain numbers and lists, and other
    unserializable values become text. Anything over 64 KB, or that can't be
    serialized at all, is replaced by a short note. None of this ever raises,
    so a logging problem can't stop the car.
  - The module now adds `../shared` to the import path itself, so it can be
    imported without `server.py`.
  - The header docstring now describes the pipeline contract, `configure`
    and `options`, `details`, `session_dir`, `seq == 1`, and the 1 s / 5 s
    timing budget.
- `server/server.py`:
  - `details` is written to each frame's line in `log.jsonl`. It is not sent
    to the Pi.
  - `meta["session_dir"]` gives the recording folder's full path (`None` when
    recording is off).
  - `session.json` gains `decider_target`.
- `server/sign_decider.py`: returns `details` = `{"acting_on": <sign or
  null>, "reason": "detected" | "held" | "no_sign", plus
  "_unknown_class" when the sign isn't in ACTIONS}`. This makes held-sign
  frames readable in the log.
- Tested end to end with a fake pipeline module. Normal details, a string,
  numpy values, an oversized blob, an exception (the car got a stop and
  the error was logged), options received, and a debug file written to
  `session_dir` all behaved as described. Also tested `sign_decider` with
  the CNN call mocked, covering stop, unknown class and no sign.

### Restore the 1 s safety timeout default

- `pi/pi_client.py` built-in default and `pi/pi_config.example.json`:
  `motors.safety_timeout_s` changed from 10.0 to 1.0. Your own
  `pi_config.json` keeps whatever it has (10.0 on 11/6).

### Make wheel_test.py read pi_config.json

- `pi/wheel_test.py`:
  - Reads the wiring, reverse flags and I2C address from
    `pi/pi_config.json`, or the file given with `--config`.
  - Motors can be given as terminals (`1`–`4`), names (`front_left`) or
    `all`. Output shows each motor's name and why it was reversed.
  - `--reverse` still works and replaces the config's flags. `--address`
    overrides the config. `--no-config` restores the old raw behavior.
  - Warns if the config file is missing.
  - Imports `pi_client.py` for the defaults, so it's no longer standalone.
    It still needs only the Adafruit library to run.
  - `--power` is still applied directly, ignoring `max_throttle`.
- Tested with a stand-in for the motor library: config reversal (M1, M4),
  names, `--reverse 2 3`, `--no-config`, an unknown name and a missing
  config.

### Update docs for the image pipeline

- `README.md`:
  - Diagram shows the pipeline with the temporary stand-in.
  - New section "Plugging in the image pipeline".
  - `wheel_test` usage updated.
  - Config table: safety timeout 1.0, new `decider.options`.
  - Note on using the laptop's `.local` name.
  - `sign_decider.py` and `cnn_api_client.py` marked temporary.
- `docs/KNOWN_ISSUES.md`: reorganized into pipeline connection, temporary
  stand-in, and car/operations. New items:
  - a hung `decide()` locks the car out until a restart
  - no time limit on the `python` backend, which matters on a CPU-only laptop
  - native crashes take the server down
  - no session start/end hooks
  - the `http` backend doesn't pass `options`

  Fixed items moved to "Resolved". Items dropped by decision are noted.
- `CHANGELOG.md`: this section, plus the history note in the first section.

## 2026-11-07: Repository created

Merged from two folders, both called `AV_Scripts`: one from the Pi and one
from the laptop. Each entry below is one git commit, oldest first, so
`git log` shows the same steps. Code behavior was not changed in any commit;
the only code edits are the import-path lines and message text noted under
"Restructure".

### Baseline: code exactly as it ran on 2026-11-06

Files were taken as they were, with these choices:

| File | Taken from | Why |
|---|---|---|
| `pi_client.py` | Pi | The laptop's copy is from 10/27 and has `safety_timeout_s` 1.0 in its defaults. The Pi's copy (11/6) has 10.0 and is the one that ran. |
| `pi_config.json` → `pi_config.example.json` | Pi | The laptop's copy is the original template (placeholder host and password, no reverse flags). |
| `protocol.py`, `wheel_test.py` | Pi | Identical on both machines. |
| `server.py`, `hookup.py`, `sign_decider.py`, `cnn_api_client.py` | Laptop | Only exist there. `cnn_api_client.py` is a teammate's file, included unmodified. |
| `server_config.json` → `server_config.example.json` | Laptop | |
| `README.md`, `Local_Instructions.md` | Laptop | Only exist there. The README is the original version from 10/27. |

- **Identifying details removed from all history.** The temporary CNN
  client is stored as `cnn_api_client.py` from the first commit on (the
  original file was named after a teammate), docs refer to "a teammate", and
  the CNN API's hostname is replaced by `<cnn-api-host>`. So this baseline
  differs from what ran in one way: `cnn_api_client.py` holds a placeholder
  URL. The history was rewritten before the repository was ever pushed.
- **Password removed.** In both example configs, the real password was
  replaced with the placeholder `change-me`. Nothing else in the configs
  changed: the host, timeouts, wiring and decider are as they ran.
- **Real configs ignored.** `.gitignore` excludes `pi_config.json` and
  `server_config.json`, so the password can't be committed by accident. It
  also excludes `recordings/`, `camera_test.*`, `__pycache__/` and venv
  folders.
- **Not included:**
  - The laptop's outdated `pi_client.py` and `pi_config.json` (see table).
  - Both `__pycache__/` folders (compiled files).
  - `recordings/`, `recordings/all recordings/` and an image archive: all
    runtime output (frames and logs from test drives).
- **Not found on either machine:** `pattern_decider.py` and the newer README,
  both of which the original design notes describe.

### Restructure into pi/, server/ and shared/

- Moved:
  - `pi_client.py`, `wheel_test.py`, `pi_config.example.json` → `pi/`
  - `server.py`, `hookup.py`, `sign_decider.py`, `cnn_api_client.py`,
    `server_config.example.json` → `server/`
  - `protocol.py` → `shared/`, the single copy for both sides
  - `README.md` → `docs/README_original.md`, content unchanged
  - `Local_Instructions.md` → `docs/Local_Instructions.md`, content
    unchanged in this commit
- `pi/pi_client.py`: added one line before `import protocol` that puts
  `../shared` on the import path.
- `server/server.py`: added `import sys` and the same line before
  `import hookup`. `hookup.py` and `sign_decider.py` are unchanged, since they
  are imported through `server.py`.
- `shared/protocol.py`: the docstring and two version-mismatch error messages
  said to copy `protocol.py` to both machines by hand. They now say to keep
  both machines on the same repo version.
- Config files are still looked for next to each script, so they now go in
  `pi/` and `server/`.
- Verified before and after with a full loop on one computer (test-pattern
  camera, dry-run motors, dummy decider at 0.3, recording on). The baseline
  recorded 72 frames and the restructured code 73 in about 5 s, with the
  motors receiving commands and stopping on disconnect. Also checked that
  `sign_decider:decide` still imports through `hookup.configure`, without
  calling the CNN.

### Fix Local_Instructions for the new layout

`docs/Local_Instructions.md`:
- Renumbered the steps 1–9. The original had two Step 4s, and Step 3
  came after one of them. The order of the steps is unchanged.
- Server step: `cd ~/carlink/server` before `python3 server.py`.
- Pi step: `cd ~/carlink/pi` instead of `cd AV_Scripts/`.
- Client command: `~/carlink-venv/bin/python pi_client.py --dry-run` instead
  of `python client.py --dry-run`. The script is named `pi_client.py`, and
  the venv's Python is what has the Adafruit library, which answers the
  original note.
- Added a line saying it assumes the repo is cloned as `~/carlink`, and code
  formatting for the commands.

### Add requirements.txt for the Pi and the server

- `pi/requirements.txt`: `adafruit-circuitpython-motorkit`, with a note
  that OpenCV comes from apt.
- `server/requirements.txt`: `requests`, `numpy`, `opencv-python-headless`,
  with the apt alternative noted.
- Versions are unpinned because the installed versions are unknown.

### Add known issues list

- `docs/KNOWN_ISSUES.md`: 17 items found while checking the code and
  recordings, including CNN latency and reconnect loops, timeouts that don't
  line up, the 10 s safety timeout vs the docs, unmapped CNN classes, false
  positives, and the local-CNN plan. Nothing was fixed.

### Add new README for the repository

- `README.md`: new. Covers the layout, setup from a git clone, creating
  configs from the templates, running, bring-up tests, testing with no
  hardware, current configuration values and hardware. The original stays at
  `docs/README_original.md`.

### Add this changelog

- `CHANGELOG.md`: this file.
