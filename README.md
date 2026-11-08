# carlink: Pi sign-following car

A proof-of-concept autonomous model car. A Raspberry Pi on a 4WD chassis sends
webcam frames over the local network to a laptop. The laptop asks a
sign-detection CNN what it sees, turns the answer into four motor values, and
sends them back. The Pi drives the motors and sends the next frame.

```
 Pi  (pi/pi_client.py)                       Laptop  (server/server.py)
 ─────────────────────                       ──────────────────────────
 newest webcam frame ── frame (JPEG, TCP) ─▶ hookup.decide(image, meta)
                                               └─ sign_decider.decide
                                                    └─ cnn_api_client.classify
                                                         └─ CNN service (HTTPS)
 set 4 motors ◀──────── command ──────────── label, score, 4 motor values
       └─▶ repeat                              └─▶ saved to recordings/
```

The Pi sends one frame, waits for the command, then sends the next. The
frame rate is therefore 1 / (network round trip + CNN time). Right now the
CNN is the bottleneck; see [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).

## Repository layout

| Path | Runs on | What it is |
|---|---|---|
| `pi/pi_client.py` | Pi | Car side: camera thread, motor control with safety stop, auto-reconnecting client. Test modes `--test-camera`, `--test-motors`, `--dry-run`. |
| `pi/wheel_test.py` | Pi | Standalone motor test. Spins chosen Bonnet terminals for a few seconds. |
| `pi/pi_config.example.json` | Pi | Template for `pi/pi_config.json`. |
| `server/server.py` | Laptop | TCP server: accepts one car, calls the decider for each frame, records everything. |
| `server/hookup.py` | Laptop | **The integration point**: `decide(image_bytes, meta)` and the `dummy` / `python` / `http` backends. Read the top of this file. |
| `server/sign_decider.py` | Laptop | Turns the CNN's detected sign into motor values (`ACTIONS` table). |
| `server/cnn_api_client.py` | Laptop | A teammate's client for the CNN service: `classify(frame)`. To be replaced by a local CNN. |
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
~/carlink-venv/bin/python pi_client.py --dry-run  # logs motor values instead
```

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
~/carlink-venv/bin/python wheel_test.py 1 2 3 4 --power 0.5 --reverse 1 4
```

`wheel_test.py` doesn't read the config, so pass `--reverse` for the terminals
reversed in `motors.wiring` (currently 1 and 4).

### Without any hardware

Set `camera.device` to `"test"` (a generated image) and run the Pi side with
`--dry-run`. Run the server with `decider.backend` set to `"dummy"`. With those
settings the whole loop runs on one computer, with `server.host` set to
`127.0.0.1`.

## Configuration

Values in the table are the ones in the example configs, which match what the
car ran on 2026-11-06. The full settings reference is in
[docs/README_original.md](docs/README_original.md#settings). Note that
`motors.safety_timeout_s` is now 10.0, not the 1.0 shown there.

| Setting | Value | Notes |
|---|---|---|
| Pi `server.host` | `<laptop-ip>` | The laptop's IP on the hotspot. Changes with DHCP. |
| Pi `server.reply_timeout_s` | `5.0` | No reply this long: reconnect. |
| Pi `motors.safety_timeout_s` | `10.0` | No command this long: stop the motors. |
| Pi `motors.max_throttle` / `min_throttle` | `0.6` / `0.0` | A command of 1.0 gives 0.6 of battery voltage. |
| Pi `motors.wiring` | M1 front left (reversed), M2 rear left, M3 front right, M4 rear right (reversed) | |
| Pi `camera` | 320x240 JPEG, quality 80 | |
| Server `decider` | `python`, `sign_decider:decide` | `dummy` with `dummy_throttle` for testing without the CNN. |
| Server `recording.enabled` | `true` | About 0.5 GB per hour of driving. |

Driving behavior lives in `server/sign_decider.py`: the `ACTIONS` table (CNN
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
