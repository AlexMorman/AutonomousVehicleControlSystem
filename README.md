# Car link

Connects the Pi on the car to the server. The Pi sends webcam frames over TCP;
the server passes each one to the classifier / motor-control code and sends
back four motor values; the Pi applies them and sends the next frame.

```
 Pi (pi_client.py)                          Server (server.py)
 ─────────────────                          ──────────────────
 newest webcam frame ──── frame (JPEG) ───▶ hookup.decide(image, meta)
                                              │  (classifier + motor logic,
                                              │   your colleague's code)
 set 4 motors ◀────────── command ────────── label, score, 4 motor values
       │                                      └─▶ saved to recordings/
       └──▶ repeat
```

## Files

| File | Goes on | What it is |
|---|---|---|
| `protocol.py` | **both** | Message format and password check. Keep the two copies identical. |
| `pi_client.py` | Pi | The car side. |
| `pi_config.json` | Pi | Car settings. |
| `server.py` | Server | The server side. |
| `hookup.py` | Server | **The hook-up point for the classifier.** Read the top of this file. |
| `server_config.json` | Server | Server settings. |

## Setup

### Pi (Raspberry Pi 3B, Raspberry Pi OS)

```bash
sudo raspi-config                  # Interface Options -> I2C -> Enable, then reboot
sudo apt update
sudo apt install -y python3-opencv python3-venv i2c-tools v4l-utils
python3 -m venv --system-site-packages ~/carlink-venv
~/carlink-venv/bin/pip install adafruit-circuitpython-motorkit
```

OpenCV comes from `apt`, not `pip`, because building it with pip on a Pi 3B can
take hours or run out of memory. `--system-site-packages` lets the virtual
environment see it.

Copy `protocol.py`, `pi_client.py` and `pi_config.json` into one folder on the Pi.

### Server

Python 3.9 or newer. The scripts themselves need nothing beyond the standard
library. `hookup.decode_image()` needs `pip install numpy opencv-python-headless`,
and your colleague's classifier will have its own requirements.

Copy `protocol.py`, `server.py`, `hookup.py` and `server_config.json` into one
folder on the server.

### Both

1. Set the same password in `pi_config.json` (`server.password`) and
   `server_config.json` (`password`). Alternatively set the environment variable
   `CARLINK_PASSWORD` on both machines; it overrides the files.
2. In `pi_config.json`, set `server.host` to the server's IP address.
3. If the server has a firewall, allow TCP port 5005 (or whichever port you set).

## Bring-up order

Run all Pi commands from the folder holding the scripts, using the venv's
Python: `~/carlink-venv/bin/python pi_client.py ...`

1. **Bonnet visible:** `i2cdetect -y 1` should list `60`.
2. **Camera:** `pi_client.py --test-camera` saves `camera_test.jpg`. Open it to
   check the view. If no camera is found, `v4l2-ctl --list-devices` shows what
   to put in `camera.device`.
3. **Motors (wheels off the ground):** `pi_client.py --test-motors` spins
   front left, front right, rear left, rear right forward in turn, 2 s each.
   - Wrong wheel: fix `terminal` in `motors.wiring`.
   - Wrong direction: set that motor's `reverse` to `true`.
   - Try `--throttle 0.3`, `0.5`, `0.7` and so on. Each line logs the command
     and the power it maps to; note the lowest power at which the wheels turn
     and set `min_throttle` to it. Expect stronger results on the floor than in
     the air, so leave some margin.
4. **Network, with the car still:** run `python3 server.py` on the server
   (default backend `dummy`, all motors 0), then `pi_client.py` on the Pi. The
   Pi logs frames per second and round-trip time every 5 s, and frames appear
   under `recordings/` on the server.
5. **Network, motors moving (wheels off the ground):** set
   `decider.dummy_throttle` to 0.3 in `server_config.json` and restart the
   server. All four wheels should turn. Stop the server and they should stop
   within the safety timeout.
6. **Classifier:** point `decider` at your colleague's code (see `hookup.py`).

Stop either script with Ctrl+C. The Pi stops the motors on any exit.

`pi_client.py --dry-run` runs everything without the Bonnet, logging motor
values instead (add `--verbose` to see them). Setting `camera.device` to
`"test"` replaces the webcam with a generated test image. With both, you can
run the full loop on any computer.

## Settings

### Pi (`pi_config.json`)

| Setting | Default | Meaning |
|---|---|---|
| `server.host`, `server.port` | `192.168.1.50`, `5005` | Where the server is. |
| `server.password` | `change-me` | Must match the server. |
| `server.reply_timeout_s` | `5.0` | No reply this long: drop the connection and reconnect. |
| `server.reconnect_delay_s` | `2.0` | Wait between reconnect attempts. |
| `camera.device` | `0` | `0` for `/dev/video0`, a device path, or `"test"`. |
| `camera.width`, `camera.height` | `320`, `240` | Size of the frames sent. Frames are resized if the camera delivers a different size. |
| `camera.fps` | `15` | Rate requested from the camera. |
| `camera.format` | `jpeg` | `jpeg`, `png` or `raw` (see below). |
| `camera.jpeg_quality` | `80` | 1–100. |
| `motors.safety_timeout_s` | `1.0` | No command this long: stop the motors. |
| `motors.max_throttle` | `0.6` | Power applied for a command of ±1 (1.0 = full battery voltage). |
| `motors.min_throttle` | `0.0` | Power applied for the smallest non-zero command. Set it to where the motors start turning, so small commands still move the car. |
| `motors.deadband` | `0.05` | Commands smaller than this are treated as 0. |
| `motors.wiring` | M1 front left, M2 rear left, M3 front right, M4 rear right | Which Bonnet terminal each motor is on, and whether to reverse it. |
| `motors.driver` | `bonnet` | `dry-run` to run without the Bonnet. |
| `stats_every_s` | `5.0` | How often to log the frame rate. `0` turns it off. |

**Image formats.** `jpeg` is the default: it works with every webcam and keeps
frames to roughly 5–20 KB. `png` is lossless but about 5–10 times larger. `raw`
sends uncompressed pixels (width × height × 3 bytes, blue-green-red, about
230 KB at 320x240), which is slow over the Pi 3B's Wi-Fi.

### Server (`server_config.json`)

| Setting | Default | Meaning |
|---|---|---|
| `listen.host`, `listen.port` | `0.0.0.0`, `5005` | Address to listen on. `0.0.0.0` means all network interfaces. |
| `password` | `change-me` | Must match the Pi. |
| `idle_timeout_s` | `10.0` | No frame this long: drop the car's connection. |
| `recording.enabled` | `true` | Save every frame and decision. |
| `recording.directory` | `recordings` | Where sessions are saved. |
| `decider.backend` | `dummy` | `dummy`, `python` or `http`. See `hookup.py`. |
| `decider.target` | | `python` backend: `module_name:function_name`. |
| `decider.url` | | `http` backend: the service's URL. |
| `decider.timeout_s` | `2.0` | `http` backend: how long to wait for an answer. |
| `decider.dummy_throttle` | `0.0` | `dummy` backend: value sent to all four motors. |

## Recordings

Each connection gets its own folder:

```
recordings/2026-10-26_19-30-05_192-168-1-60/
    session.json                       client address, start and end time, frame count
    log.jsonl                          one line per frame
    frame_000001_19-30-05.123.jpg      frame number and time received
    frame_000002_19-30-05.201.jpg
    ...
```

Each line of `log.jsonl` holds: `seq`, `file`, `capture_time` (Pi clock),
`received_time` (server clock), `label`, `score`, `motors` (the command sent),
`decide_ms` (time spent in `decide()`), `previous_round_trip_ms`, image
`format`/`width`/`height`, and `error` (null unless `decide()` failed). The two
clocks aren't synchronized, so compare times within one clock only.

Reading it in Python:

```python
import pandas as pd
df = pd.read_json("recordings/<session>/log.jsonl", lines=True)
```

At 320x240 JPEG and 10 frames per second, expect roughly 0.5 GB per hour.

## Behavior and safety

- The car keeps each command until the next one arrives.
- The motors stop if no command arrives within `motors.safety_timeout_s`, if the
  connection drops, or if `pi_client.py` exits for any reason (Ctrl+C, `kill`,
  an error). The safety stop runs on its own thread, so it works even if the
  network code is stuck.
- If `decide()` raises an error or returns something malformed, the server
  sends a stop command for that frame, records the error and continues.
- The Pi reconnects automatically if the server restarts or Wi-Fi drops.
- Only one car can connect at a time; a second is refused and keeps retrying.
- The password check stops strangers from driving the car, but traffic is not
  encrypted. Keep the car on a network you control.

## Performance notes

- The car waits for each answer before sending the next frame, so the frame
  rate is roughly 1 / (network round trip + `decide()` time).
- The Pi 3B has 2.4 GHz-only Wi-Fi. If round trips spike to 100 ms or more, turn
  off Wi-Fi power saving: `sudo iw dev wlan0 set power_save off`.
- The Pi script uses roughly 70–100 MB of RAM, mostly OpenCV.

## Protocol

See the top of `protocol.py` for the message format. In short, every message is
a pair of 4-byte lengths, a JSON header and an optional binary payload (the
image). The Pi connects to the server, answers a password challenge, then sends
`frame` messages and receives one `command` message per frame.
