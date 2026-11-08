# carlink: Pi sign-following car

A proof-of-concept autonomous model car. A Raspberry Pi on a 4WD chassis sends
webcam frames over the local network to a laptop. The laptop asks a
sign-detection CNN what it sees, turns the answer into four motor values, and
sends them back. The Pi drives the motors and sends the next frame.

```
 Pi  (pi/pi_client.py)                       Laptop  (server/server.py)
 ─────────────────────                       ──────────────────────────
 newest webcam frame ── frame (JPEG, TCP) ─▶ hookup.decide(image, meta)
                                               └─ image pipeline (black box):
                                                  cropping, CNN, defenses
                                                  [today: temporary stand-in,
                                                   sign_decider + cnn_api_client]
 set 4 motors ◀──────── command ──────────── 4 motor values (+ label, score)
       └─▶ repeat                              └─▶ frame + details saved
                                                   to recordings/
```

The Pi sends one frame, waits for the command, then sends the next. The
frame rate is therefore 1 / (network round trip + CNN time). Right now the
CNN is the bottleneck; see [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).

## Repository layout

| Path | Runs on | What it is |
|---|---|---|
| `pi/pi_client.py` | Pi | Car side: camera thread, motor control with safety stop, auto-reconnecting client. Test modes `--test-camera`, `--test-motors`, `--dry-run`. |
| `pi/wheel_test.py` | Pi | Motor test: spins chosen motors for a few seconds, using the config's wiring. |
| `pi/tests/` | Pi | Two manual hardware tests: every wheel at full power, and one photo sent to the server. |
| `pi/pi_config.example.json` | Pi | Template for `pi/pi_config.json`. |
| `server/server.py` | Laptop | TCP server: accepts one car, calls the decider for each frame, records everything. |
| `server/pipeline/` | Laptop | **Where the image pipeline (black box) goes.** See its README. |
| `server/hookup.py` | Laptop | **The integration point**: `decide(image_bytes, meta)` and the `dummy` / `python` / `http` backends. Read the top of this file. |
| `server/sign_decider.py` | Laptop | **Temporary.** Turns the CNN's detected sign into motor values (`ACTIONS` table). Replaced by the image pipeline. |
| `server/cnn_api_client.py` | Laptop | **Temporary.** Client for the remote CNN API, used by `sign_decider.py`. URL set in `server_config.json`. |
| `server/server_config.example.json` | Laptop | Template for `server/server_config.json`. |
| `shared/protocol.py` | Both | Message framing, password check, config loading. Imported by both sides. |
| `docs/` | | Original README, laptop/hotspot run-book, known issues. |

The same repository is cloned on both machines; each runs only its own
folder. Keep both on the same commit, since `shared/protocol.py` must match.

## Setup

### Both machines

```bash
git clone <repo-url> ~/carlink
```

Real config files are not in git because they hold the shared password.
Create them from the templates:

```bash
cp ~/carlink/pi/pi_config.example.json         ~/carlink/pi/pi_config.json          # on the Pi
cp ~/carlink/server/server_config.example.json ~/carlink/server/server_config.json  # on the laptop
```

Then set the **same password** in both (`server.password` on the Pi,
`password` on the laptop). Alternatively, set the `CARLINK_PASSWORD` environment
variable on both machines; it overrides the files.

### Pi (Raspberry Pi 3B, Raspberry Pi OS)

```bash
sudo raspi-config        # Interface Options -> I2C -> Enable, then reboot
sudo apt update
sudo apt install -y python3-opencv python3-venv i2c-tools v4l-utils
python3 -m venv --system-site-packages ~/carlink-venv
~/carlink-venv/bin/pip install -r ~/carlink/pi/requirements.txt
i2cdetect -y 1           # should list 60 (the motor Bonnet)
```

OpenCV comes from apt because building it with pip on a Pi 3B takes hours.
`--system-site-packages` lets the venv see it.

In `pi/pi_config.json`, set `server.host` to the laptop's IP address.

### Laptop (Debian 13)

```bash
sudo apt install -y python3-requests python3-numpy python3-opencv
```

Debian blocks the system `pip`. To use pip instead of apt, make a venv and run
`pip install -r server/requirements.txt`. If a firewall is enabled, allow TCP
port 5005.

## Running

Laptop:

```bash
cd ~/carlink/server
python3 server.py            # add --verbose to log every frame
```

Pi:

```bash
cd ~/carlink/pi
~/carlink-venv/bin/python pi_client.py            # drives the motors
~/carlink-venv/bin/python pi_client.py --verbose  # also prints every frame
~/carlink-venv/bin/python pi_client.py --dry-run  # no Bonnet; add --verbose to see values
```

With `--verbose`, the Pi prints one line per frame showing the command it
received and the power it actually applied to each Bonnet terminal, after
`max_throttle`, the deadband and the reverse flags:

```
seq 2: dummy (1.00), 0 ms | command FL +1.00 FR +1.00 RL +1.00 RR +1.00 | power M1 -60% M2 +60% M3 +60% M4 -60%
```

Everything goes to the console; the Pi writes nothing to disk.

Stop either side with Ctrl+C. The Pi stops the motors whenever it exits or
loses the connection.

Recordings are written to `recordings/` under the folder you start the server
from. With the commands above that's `server/recordings/`, which git ignores.

For the step-by-step routine over the laptop's hotspot (ssh, finding the IP),
see [docs/Local_Instructions.md](docs/Local_Instructions.md).

### Bring-up and tests (Pi)

Lift the wheels off the ground for motor tests.

```bash
~/carlink-venv/bin/python pi_client.py --test-camera        # saves camera_test.jpg
~/carlink-venv/bin/python pi_client.py --test-motors        # each wheel forward in turn, 2 s
~/carlink-venv/bin/python wheel_test.py all --power 0.5    # all four forward, 5 s
~/carlink-venv/bin/python wheel_test.py front_left --seconds 2
```

`wheel_test.py` takes the wiring, reverse flags and I2C address from
`pi_config.json`, so "forward" means forward on the car. `--no-config` drives
the raw terminals instead. Its `--power` is applied directly, ignoring
`max_throttle`.

### Manual tests

Neither is automated. The file names deliberately don't start with `test_`,
so pytest never runs them by accident.

```bash
cd ~/carlink/pi
~/carlink-venv/bin/python tests/wheels_full_speed.py   # wheels off the ground!
~/carlink-venv/bin/python tests/photo_to_server.py     # server must be running
```

- `wheels_full_speed.py` spins each wheel in turn at 100% power for 2 s,
  using the config's wiring and reverse flags, and prints which motor it's
  driving. Every wheel should turn forward.
- `photo_to_server.py` takes one photo, sends it to the server like a normal
  frame, and prints the label, score, motor command and response time. It
  never moves the motors and writes nothing on the Pi. Stop `pi_client.py`
  first, because the server takes one car at a time. Run the server with
  `--verbose` to see the decider's `details` too.

### Without any hardware

Set `camera.device` to `"test"` (a generated image) and run the Pi side with
`--dry-run`. Run the server with `decider.backend` set to `"dummy"`. With those
settings the whole loop runs on one computer, with `server.host` set to
`127.0.0.1`.

## Plugging in the image pipeline

The pipeline is a black box to the rest of the system, and `server/hookup.py`
is the only place it connects. The full contract is at the top of that file.
In short:

1. Write a module with `decide(image_bytes, meta)` that returns
   `{"motors": {"front_left": x, "front_right": x, "rear_left": x,
   "rear_right": x}}`, each value from -1 to 1. Optional fields are
   `"label"`, `"score"` and `"details"`.
2. Put it in `server/pipeline/` as a package (see
   [server/pipeline/README.md](server/pipeline/README.md)) and point the
   config at it:
   ```json
   "decider": {"backend": "python", "target": "pipeline:decide",
               "options": {"model_path": "...", "anything": "the module needs"}}
   ```
   If the module defines `configure(options)`, it's called once at startup
   with `options`. Load models at import time or in `configure`, not per
   frame. If it defines `__version__`, that version is written to every
   recording's `session.json`.
3. **Logging:** whatever the pipeline puts in `details` (detections, crop
   boxes, which defense fired, step timings) is written to that frame's line
   in `recordings/<session>/log.jsonl`. The pipeline can also save its own
   files (such as crops) to `meta["session_dir"]`. `decide_ms` records how
   long each frame took.
4. **Timing:** answer well inside 1 s. Slower frames make the car stop and
   start; after 5 s it reconnects. See
   [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) section 1.
5. **Images:** `image_bytes` is the JPEG exactly as the Pi encoded it.
   `hookup.decode_image(image_bytes, meta)` turns it into an OpenCV BGR array.
   Decode once and pass arrays between pipeline steps; re-encoding to JPEG
   inside the pipeline would add a second round of compression.

If the pipeline raises an exception or returns something malformed, the car
gets a stop command for that frame and the error is logged. To run the
pipeline as a separate local process instead, use the `http` backend (see
`hookup.py`).

## Configuration

Values in the table are the ones in the example configs. The full settings
reference is in [docs/README_original.md](docs/README_original.md#settings).

| Setting | Value | Notes |
|---|---|---|
| Pi `server.host` | `<laptop-ip>` | The laptop's IP on the hotspot. Changes with DHCP. |
| Pi `server.reply_timeout_s` | `5.0` | No reply this long: reconnect. |
| Pi `motors.safety_timeout_s` | `1.0` | No command this long: stop the motors. Raise it in your own config while using the slow temporary CNN API. |
| Pi `motors.max_throttle` / `min_throttle` | `0.6` / `0.0` | A command of 1.0 gives 0.6 of battery voltage. |
| Pi `motors.wiring` | M1 front left (reversed), M2 rear left, M3 front right, M4 rear right (reversed) | |
| Pi `camera` | 320x240 JPEG, quality 80 | |
| Server `decider` | `python`, `sign_decider:decide` | `dummy` for testing without the CNN. Its `dummy_throttle` is 1.0, so all wheels run at full configured speed as soon as the car connects. |
| Server `decider.options` | `{"cnn_api_url": ""}` | Passed to the decider module's `configure()`. Put the real CNN API URL here in your own config. |
| Server `recording.enabled` | `true` | About 0.5 GB per hour of driving. |

In one line: **a command of 1.0 means 60% power to motors 1, 2, 3 and 4, with
motors 1 and 4 reversed.** These values are fixed for this one car.

Until the pipeline replaces it, driving behavior lives in
`server/sign_decider.py`: the `ACTIONS` table (CNN
class name to left/right motor values), `NO_SIGN` (currently full speed
ahead), `UNKNOWN_SIGN` (stop), `MIN_CONFIDENCE` and `HOLD_SECONDS`.

## Hardware

Raspberry Pi 3B with a Waveshare UPS HAT (E) (4x 21700 cells) for the Pi;
Adafruit DC & Stepper Motor Bonnet at I2C 0x60 on a GPIO riser; 4WD acrylic
chassis with four TT gear motors on a separate 4xAA pack; USB webcam. The
server is a laptop running Debian 13.

## More

- [CHANGELOG.md](CHANGELOG.md): every change made when this repository was
  created from the two machines' folders.
- [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md): open problems, roughly in
  priority order.
- `shared/protocol.py` (top of file): the wire protocol.
